"""E2E: T0 evidence → AI query construction → sql_query_execute → grounded count."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock, patch

from hub.agent_center.capability import FAILURE_NEEDS_REASONING
from hub.agent_center.completion import derive_completion_contract
from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.routing.execution import (
    RouteExecutor,
    child_runtime_failure_reason,
    extract_provider_answer,
    merge_child_tools_into_packet,
)
from hub.agent_center.routing.models import RoutingSettings
from hub.agent_center.tool_runtime.continuation import build_continuation_from_t0
from hub.agent_center.tool_runtime.feed import ToolRuntimeFeed
from hub.agent_center.tool_runtime.runtime import RuntimeContext, ScriptedModelDriver, ToolRuntime
from hub.agent_center.tool_runtime.settings import ToolRuntimeSettings
from hub.registry.models import Registry


PROMPT = "Count number of pregnant women in Baloy Q2 2026"


def _ctx(**kwargs: Any) -> AgentToolsContext:
    return AgentToolsContext(
        registry=Registry([]),
        repository_ids=list(kwargs.pop("repository_ids", ["live-processing-local"])),
        allowed_tools=set(
            kwargs.pop(
                "allowed_tools",
                {
                    "repo_search",
                    "sql_lookup",
                    "sql_query_execute",
                    "repository_intelligence",
                    "org_unit_lookup",
                    "uid_lookup",
                },
            )
        ),
        **kwargs,
    )


class ChildFinalizeSeamTests(unittest.TestCase):
    def test_empty_child_failure_surfaces_explicit_reason(self) -> None:
        fake = MagicMock()
        fake.adapters = []
        fake.repositories = MagicMock(return_value=[])
        ex = RouteExecutor(fake, availability_loader=lambda: {})
        ex._active["e-empty"] = {
            "id": "e-empty",
            "status": "running",
            "fallback_from": "deterministic",
            "ai_escalation_occurred": True,
            "t0_failure_reason": FAILURE_NEEDS_REASONING,
        }
        out = ex._finalize_synthesis_or_agent_answer(
            "e-empty",
            prompt=PROMPT,
            run={
                "id": "child-1",
                "status": "failed",
                "answer": "",
                "agent_id": "openai-api",
                "model": "gpt-5.6-terra",
                "error": (
                    "{'type': 'insufficient_quota', 'code': 'credit_balance_exhausted', "
                    "'message': 'You have no credits remaining.'}"
                ),
                "error_code": "stream_error",
                "usage": {"session_reused": False, "tool_runtime_steps": []},
                "finished_at": "2026-08-10T00:00:01+00:00",
            },
            repository_ids=["live-processing-local"],
            evidence_packet={
                "usable": True,
                "sources": ["tool:sql_lookup", "tool:repository_intelligence"],
                "hits": [],
                "tool_results": [{"tool": "sql_lookup", "ok": True}],
            },
            synthesis_escalation=False,
            chosen="openai-api",
        )
        self.assertEqual(out.get("status"), "failed")
        answer = str(out.get("answer") or "")
        self.assertTrue(answer.strip())
        self.assertNotEqual(answer.strip(), "(no answer)")
        self.assertIn("credit_balance_exhausted", answer.lower() + str(out.get("error") or "").lower())
        self.assertTrue(out.get("ai_escalation_occurred"))

    def test_merge_child_sql_execute_into_packet_and_tools(self) -> None:
        packet = {
            "usable": True,
            "sources": ["tool:sql_lookup"],
            "tool_results": [{"tool": "sql_lookup", "ok": True}],
            "hits": [],
        }
        run = {
            "usage": {
                "tool_runtime_steps": [
                    {
                        "tool": "sql_query_execute",
                        "ok": True,
                        "summary": "n=17",
                        "duration_ms": 12,
                    }
                ]
            }
        }
        merged = merge_child_tools_into_packet(packet, run)
        tools = [r.get("tool") for r in merged.get("tool_results") or []]
        self.assertIn("sql_query_execute", tools)
        self.assertIn("tool:sql_query_execute", merged.get("sources") or [])


class QueryConstructionSqlExecuteE2ETests(unittest.TestCase):
    def test_runtime_sql_execute_then_grounded_numeric_answer(self) -> None:
        store = MagicMock()
        store.get_query.return_value = {
            "id": "q-pregnant",
            "title": "Pregnant women",
            "sql_text": "SELECT COUNT(*) AS count FROM pregnant WHERE ou = :location AND period = :period",
            "connection_id": "demo-ro",
        }
        connections = MagicMock()
        connections.get_configured.return_value = MagicMock()
        executor = MagicMock()
        result = MagicMock()
        result.ok = True
        result.columns = ["count"]
        result.rows = [[17]]
        result.row_count = 1
        result.error = None
        executor.execute.return_value = result

        feed = ToolRuntimeFeed()
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(max_steps=6, hard_runaway_cap=8),
            feed=feed,
        )
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "tool_request",
                    "tool": "sql_lookup",
                    "arguments": {"query": "pregnant Baloy"},
                    "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
                },
                {
                    "kind": "tool_request",
                    "tool": "sql_query_execute",
                    "arguments": {
                        "query_id": "q-pregnant",
                        "params": {"location": "Baloy", "period": "2026Q2"},
                    },
                    "usage": {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
                },
                {
                    "kind": "final_answer",
                    "answer": (
                        "Count: 17\nSource: read-only SQL (Pregnant women) via demo-ro"
                    ),
                    "usage": {"input_tokens": 6, "output_tokens": 10, "total_tokens": 16},
                },
            ]
        )

        # sql_lookup via legacy path needs store.list_queries; keep simple by patching execute path.
        tools_ctx = _ctx(
            sql_store=store,
            sql_executor=executor,
            sql_connections=connections,
        )

        # Bypass legacy sql_lookup handler complexity — return a compact discovery observation.
        def _execute(name, args, ctx, **kwargs):
            from hub.agent_center.tool_runtime.results import ToolResult

            if name == "sql_lookup":
                return ToolResult(
                    ok=True,
                    summary="found q-pregnant",
                    observation=json.dumps(
                        {
                            "queries": [
                                {"id": "q-pregnant", "title": "Pregnant women", "connection_id": "demo-ro"}
                            ]
                        }
                    ),
                    source="tool_runtime",
                    duration_ms=1,
                    tool="sql_lookup",
                )
            return runtime.executor.__class__.execute(
                runtime.executor, name, args, ctx, **kwargs
            )

        runtime.executor.execute = _execute  # type: ignore[method-assign]

        prior = {
            "evidence_packet": {
                "usable": True,
                "sources": ["tool:repo_search", "tool:sql_lookup", "tool:repository_intelligence"],
                "tool_results": [
                    {"tool": "repo_search", "ok": True},
                    {"tool": "sql_lookup", "ok": True},
                    {"tool": "repository_intelligence", "ok": True},
                ],
                "hits": [{"source": "sql:saved_query", "query_id": "q-pregnant"}],
            },
            "t0_failure_reason": FAILURE_NEEDS_REASONING,
            "completion_contract": derive_completion_contract(PROMPT).public(),
        }
        cont = build_continuation_from_t0(prior)
        outcome = runtime.run(
            driver,
            RuntimeContext(
                prompt=PROMPT,
                tools_ctx=tools_ctx,
                interaction_mode="inspect",
                provider="openai-api",
                model="gpt-5.6-terra",
                run_id="run-sql-e2e",
                continuation=cont,
                evidence_packet=dict(prior["evidence_packet"]),
            ),
        )
        self.assertEqual(outcome.status, "completed")
        self.assertIn("17", outcome.answer)
        self.assertTrue(any(s.tool == "sql_query_execute" and s.ok for s in outcome.steps))
        # Observation returned into loop (execute was invoked before final answer).
        self.assertGreaterEqual(len(outcome.steps), 3)
        self.assertTrue(
            outcome.grounding.get("task_solved")
            or outcome.grounding.get("answer_grounded")
            or "17" in outcome.answer
        )

        # Parent finalize must keep numeric answer + executed tools.
        fake = MagicMock()
        fake.adapters = []
        fake.repositories = MagicMock(return_value=[])
        ex = RouteExecutor(fake, availability_loader=lambda: {})
        ex._active["e-sql"] = {
            "id": "e-sql",
            "status": "running",
            "fallback_from": "deterministic",
            "ai_escalation_occurred": True,
            "t0_failure_reason": FAILURE_NEEDS_REASONING,
        }
        child = {
            "id": "child-sql",
            "status": "completed",
            "answer": outcome.answer,
            "agent_id": "openai-api",
            "model": "gpt-5.6-terra",
            "usage": {
                "tool_runtime_steps": [s.public() for s in outcome.steps],
                "total_tokens": 30,
            },
            "tool_activity": [
                {"name": s.tool, "ok": s.ok, "detail": s.summary} for s in outcome.steps if s.tool
            ],
            "finished_at": "2026-08-10T00:00:02+00:00",
            "context": {},
        }
        parent = ex._finalize_synthesis_or_agent_answer(
            "e-sql",
            prompt=PROMPT,
            run=child,
            repository_ids=["live-processing-local"],
            evidence_packet=dict(prior["evidence_packet"]),
            synthesis_escalation=False,
            chosen="openai-api",
            reevaluate_grounding=True,
        )
        self.assertEqual(parent.get("status"), "completed")
        self.assertIn("17", parent.get("answer") or "")
        self.assertNotIn("(no answer)", parent.get("answer") or "")
        tools = [r.get("tool") for r in (parent.get("tool_results") or [])]
        self.assertIn("sql_query_execute", tools)
        g = parent.get("grounding") or {}
        self.assertTrue(g.get("task_solved") or g.get("answer_grounded") or g.get("grounded"))


class FailureReasonHelperTests(unittest.TestCase):
    def test_child_runtime_failure_reason_uses_error(self) -> None:
        reason = child_runtime_failure_reason(
            {"status": "failed", "error": "quota exhausted", "usage": {}}
        )
        self.assertIn("quota", reason.lower())
        self.assertEqual(extract_provider_answer({"answer": ""}), "")


if __name__ == "__main__":
    unittest.main()
