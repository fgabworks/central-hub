"""AiriX AI usage telemetry — T0 purity + actual provider tokens."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.db import AgentCenterDb
from hub.agent_center.routing.cost import parse_usage
from hub.agent_center.routing.execution import RouteExecutor
from hub.agent_center.routing.history import RoutingHistoryStore
from hub.agent_center.routing.models import PromptClassification, RouteRecommendation, RoutingSettings
from hub.agent_center.routing.telemetry import (
    assert_t0_telemetry_pure,
    attach_execution_telemetry,
    build_execution_telemetry,
    empty_t0_telemetry,
)


def _rec(**kwargs: Any) -> RouteRecommendation:
    c = PromptClassification(
        task_type=kwargs.get("task_type", "lookup"),
        complexity=10,
        risk="low",
        estimated_scope_files=1,
        context_size="small",
        needs_coding=False,
        needs_testing=False,
        needs_architecture=False,
        deterministic_capable=True,
        signals=list(kwargs.get("signals") or ["deterministic_capable", "simple_lookup"]),
    )
    return RouteRecommendation(
        task_type=c.task_type,
        complexity=c.complexity,
        risk=c.risk,
        recommended_agent=kwargs.get("agent", "deterministic"),
        recommended_label="Deterministic",
        recommended_tier=kwargs.get("tier", "T0"),
        alternative_agent="low-cost",
        alternative_label="Low-cost",
        confidence=0.8,
        reason="test",
        estimated_usage="Very Low",
        approval_required=False,
        classification=c,
    )


class TelemetryUnitTests(unittest.TestCase):
    def test_parse_usage_includes_cached(self) -> None:
        parsed = parse_usage(
            {"input_tokens": 100, "output_tokens": 20, "cached_input_tokens": 40, "total_tokens": 120}
        )
        self.assertEqual(parsed["cached_tokens"], 40)
        self.assertEqual(parsed["usage_source"], "actual")

    def test_t0_empty_telemetry_is_pure(self) -> None:
        tel = empty_t0_telemetry(tools_used=["org_unit_lookup"], runtime_ms=12)
        assert_t0_telemetry_pure(tel)
        self.assertEqual(tel["total_ai_tokens"], 0)
        self.assertIsNone(tel["provider"])
        self.assertIsNone(tel["model"])
        self.assertIsNone(tel["child_ai_run_id"])

    def test_ai_run_marks_unavailable_when_usage_missing(self) -> None:
        tel = build_execution_telemetry(
            {
                "mode": "ask",
                "tier": "T2",
                "provider_id": "grok",
                "adapter_id": "grok",
                "agent_run_id": "run-abc",
                "model": "grok-4",
                "usage": {},
                "started_at": "2026-08-10T00:00:00+00:00",
                "finished_at": "2026-08-10T00:00:02+00:00",
            }
        )
        self.assertTrue(tel["llm_invoked"])
        self.assertEqual(tel["execution_type"], "AI")
        self.assertEqual(tel["usage_source"], "unavailable")
        self.assertIsNone(tel["total_ai_tokens"])
        self.assertEqual(tel["child_ai_run_id"], "run-abc")
        self.assertEqual(tel["provider"], "grok")
        self.assertEqual(tel["model"], "grok-4")

    def test_ai_run_captures_actual_provider_tokens(self) -> None:
        tel = build_execution_telemetry(
            {
                "mode": "ask",
                "tier": "T3",
                "provider_id": "codex",
                "adapter_id": "codex",
                "agent_run_id": "run-1",
                "model": "gpt-5.6-sol",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 50,
                    "cached_tokens": 200,
                    "total_tokens": 1050,
                },
            }
        )
        self.assertEqual(tel["usage_source"], "actual")
        self.assertEqual(tel["input_tokens"], 1000)
        self.assertEqual(tel["output_tokens"], 50)
        self.assertEqual(tel["cached_tokens"], 200)
        self.assertEqual(tel["total_ai_tokens"], 1050)


class T0ExecutionTelemetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = MagicMock()
        self.fake.registry = MagicMock()
        self.fake.notebook = None
        self.fake.sql_store = None
        self.fake.uid_index = None
        self.fake.email = None
        self.fake.calendar = None
        self.fake.job_store = None
        self.fake.audit_store = None
        self.fake.dhis2_reports = MagicMock()
        self.fake.dhis2_reports.search_org_units.return_value = {
            "org_units": [{"id": "x", "name": "Baloy", "level": 4}],
            "source": "sqlite",
        }
        self.fake.notepad_factory = None
        self.fake.start_run = MagicMock(
            side_effect=AssertionError("T0 must never start an AI provider")
        )
        self.tmp = tempfile.TemporaryDirectory()
        self.history = RoutingHistoryStore(AgentCenterDb(Path(self.tmp.name) / "ac.db"))
        self.executor = RouteExecutor(self.fake, history=self.history)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_t0_run_never_starts_ai_provider(self) -> None:
        with self.executor._lock:
            self.executor._active["e1"] = {
                "id": "e1",
                "status": "queued",
                "provider_id": "deterministic",
                "adapter_id": None,
                "tier": "T0",
                "task_type": "lookup",
                "prompt": "Look up UID for Baloy",
                "workspace": "work",
                "actor": "owner",
            }
        out = self.executor._execute_t0(
            "e1",
            "Look up UID for Baloy",
            _rec(),
            {
                "tool_ids": ["org_unit_lookup"],
                "repository_ids": [],
                "evidence_packet": {
                    "usable": True,
                    "hits": [{"source": "dhis2:org_unit", "name": "Baloy", "uid": "x"}],
                    "sources": ["tool:org_unit_lookup"],
                    "tool_results": [
                        {"tool": "org_unit_lookup", "ok": True, "result": {"summary": "1 hit"}}
                    ],
                    "errors": [],
                    "summary": "1 hit",
                },
            },
        )
        self.fake.start_run.assert_not_called()
        tel = out.get("telemetry") or {}
        assert_t0_telemetry_pure(tel)
        self.assertFalse(tel.get("llm_invoked"))
        self.assertEqual(tel.get("total_ai_tokens"), 0)
        self.assertIsNone(tel.get("provider"))
        self.assertIsNone(tel.get("model"))
        self.assertIsNone(tel.get("child_ai_run_id"))
        self.assertEqual(tel.get("execution_type"), "Deterministic")

    def test_t0_history_persists_zero_ai_tokens(self) -> None:
        with self.executor._lock:
            self.executor._active["e2"] = {
                "id": "e2",
                "status": "queued",
                "provider_id": "deterministic",
                "adapter_id": None,
                "tier": "T0",
                "task_type": "lookup",
                "prompt": "Look up UID for Baloy",
                "workspace": "work",
                "actor": "owner",
                "prompt_fingerprint": "abc",
                "estimated_usage": "Very Low",
            }
        out = self.executor._execute_t0(
            "e2",
            "Look up UID for Baloy",
            _rec(),
            {
                "tool_ids": ["org_unit_lookup"],
                "repository_ids": [],
                "evidence_packet": {
                    "usable": False,
                    "hits": [],
                    "sources": [],
                    "tool_results": [{"tool": "org_unit_lookup", "ok": True, "result": {}}],
                    "errors": [],
                    "summary": "none",
                },
            },
        )
        # Record history as execute() would.
        self.executor._record_history(out)
        events = self.history.list_events(workspace="work", limit=5)
        self.assertTrue(events)
        ev = events[0]
        self.assertEqual(ev.get("actual_tokens"), 0)
        self.assertEqual(ev.get("input_tokens"), 0)
        self.assertEqual(ev.get("output_tokens"), 0)
        self.assertFalse(bool(ev.get("llm_invoked")))
        self.assertEqual(str(ev.get("execution_type") or ""), "Deterministic")
        self.assertEqual(str(ev.get("model") or ""), "")
        self.assertEqual(str(ev.get("child_ai_run_id") or ""), "")
        # Leaked non-zero usage on a T0 row must still stamp zeros.
        leaked = attach_execution_telemetry(
            {
                **out,
                "usage": {"input_tokens": 999, "output_tokens": 9, "total_tokens": 1008},
                "mode": "deterministic",
                "agent_run_id": None,
                "adapter_id": None,
            }
        )
        assert_t0_telemetry_pure(leaked["telemetry"])
        self.assertEqual(leaked["telemetry"]["total_ai_tokens"], 0)


class HybridAndAiTelemetryTests(unittest.TestCase):
    def test_hybrid_marks_execution_type(self) -> None:
        tel = attach_execution_telemetry(
            {
                "mode": "ask",
                "tier": "T1",
                "provider_id": "grok",
                "adapter_id": "grok",
                "fallback_from": "deterministic",
                "agent_run_id": "child-1",
                "model": "grok-mini",
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                "tool_results": [{"tool": "org_unit_lookup", "ok": True}],
                "started_at": "2026-08-10T00:00:00+00:00",
                "finished_at": "2026-08-10T00:00:01+00:00",
            }
        )["telemetry"]
        self.assertEqual(tel["execution_type"], "Hybrid")
        self.assertTrue(tel["llm_invoked"])
        self.assertEqual(tel["total_ai_tokens"], 15)
        self.assertEqual(tel["usage_source"], "actual")
        self.assertEqual(tel["child_ai_run_id"], "child-1")


class DeterministicRepoSearchTelemetryTests(unittest.TestCase):
    def test_repo_search_no_provider_renders_t0_deterministic(self) -> None:
        """Exact observed shape: deterministic repo_search, no provider child run."""
        # Sparse/missing fields that previously produced Tier T? / Type AI / LLM Yes.
        row = {
            "status": "completed",
            "provider_id": "deterministic",
            "adapter_id": None,
            "agent_run_id": None,
            "agent_run": None,
            # mode / tier intentionally omitted — must still derive T0 Deterministic.
            "grounding": {
                "grounded": True,
                "grounded_label": "Yes",
                "source": "repo_search",
            },
            "evidence_packet": {
                "usable": True,
                "sources": ["tool:repo_search"],
                "tool_results": [
                    {"tool": "repo_search", "ok": True, "result": {"summary": "1 hit"}}
                ],
                "hits": [{"source": "repo_search", "path": "README.md"}],
            },
            "tool_results": [],  # tools may live only on the evidence packet
            "started_at": "2026-08-10T00:00:00+00:00",
            "finished_at": "2026-08-10T00:00:01+00:00",
        }
        stamped = attach_execution_telemetry(row)
        tel = stamped["telemetry"]
        assert_t0_telemetry_pure(tel)
        self.assertEqual(tel["routing_tier"], "T0")
        self.assertEqual(tel["execution_type"], "Deterministic")
        self.assertFalse(tel["llm_invoked"])
        self.assertIsNone(tel["provider"])
        self.assertIsNone(tel["model"])
        self.assertIsNone(tel["child_ai_run_id"])
        self.assertEqual(tel["total_ai_tokens"], 0)
        self.assertEqual(tel["input_tokens"], 0)
        self.assertEqual(tel["output_tokens"], 0)
        self.assertIn("repo_search", tel["tools_used"])
        self.assertGreaterEqual(tel["runtime_ms"], 1000)
        # Diagnostics text must not say T? / AI / LLM Yes.
        from hub.agent_center.routing.telemetry import format_telemetry_block

        block = format_telemetry_block(tel)
        self.assertIn("Tier: T0", block)
        self.assertIn("Type: Deterministic", block)
        self.assertIn("LLM: No", block)
        self.assertIn("repo_search", block)
        self.assertNotIn("T?", block)
        self.assertNotIn("Type: AI", block)
        self.assertNotIn("LLM: Yes", block)

    def test_llm_invoked_false_without_child_run_even_if_ai_adapter_set(self) -> None:
        tel = build_execution_telemetry(
            {
                "provider_id": "grok",
                "adapter_id": "grok",
                "agent_run_id": None,
                "tier": "T2",
            }
        )
        self.assertFalse(tel["llm_invoked"])
        self.assertIsNone(tel["child_ai_run_id"])
        self.assertEqual(tel["total_ai_tokens"], 0)


if __name__ == "__main__":
    unittest.main()
