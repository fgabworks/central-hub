"""AiriX Unified Tool Runtime — Phase 2 focused tests."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.tool_runtime.continuation import (
    RuntimeContinuation,
    build_continuation_from_t0,
    fingerprint_context,
)
from hub.agent_center.tool_runtime.feed import ToolRuntimeFeed
from hub.agent_center.tool_runtime.intelligence import score_tools, select_dynamic_tools
from hub.agent_center.tool_runtime.model_policy import select_runtime_provider_model
from hub.agent_center.tool_runtime.prune import extract_grounded_facts, prune_observations
from hub.agent_center.tool_runtime.runtime import RuntimeContext, ScriptedModelDriver, ToolRuntime
from hub.agent_center.tool_runtime.session import ProviderSessionCache
from hub.agent_center.tool_runtime.settings import ToolRuntimeSettings
from hub.agent_center.tool_runtime.stuck import StuckGuard
from hub.agent_center.tool_runtime.telemetry import build_runtime_telemetry
from hub.registry.models import Registry


def _ctx(**kwargs: Any) -> AgentToolsContext:
    return AgentToolsContext(
        registry=Registry([]),
        repository_ids=list(kwargs.pop("repository_ids", ["demo"])),
        allowed_tools=set(
            kwargs.pop(
                "allowed_tools",
                {
                    "repo_search",
                    "read_file",
                    "sql_lookup",
                    "sql_query_execute",
                    "uid_lookup",
                    "org_unit_lookup",
                    "dhis2_reports_lookup",
                    "repository_intelligence",
                    "skill_recall",
                    "data_explorer_lookup",
                    "jobs_lookup",
                    "notebook_lookup",
                },
            )
        ),
        **kwargs,
    )


class DynamicToolFilteringTests(unittest.TestCase):
    def test_sql_intent_prefers_sql_tools(self) -> None:
        specs = select_dynamic_tools(
            prompt="How many eligible members in SamplePlace for 2026 Q2?",
            interaction_mode="inspect",
            completion_intent="count",
            max_tools=8,
        )
        names = [s.name for s in specs]
        self.assertIn("sql_lookup", names)
        self.assertIn("sql_query_execute", names)
        self.assertLessEqual(len(names), 8)

    def test_file_prompt_includes_repo_and_recall(self) -> None:
        specs = select_dynamic_tools(
            prompt="Where is the README module that documents architecture?",
            interaction_mode="inspect",
            completion_intent="file_search",
            max_tools=8,
        )
        names = {s.name for s in specs}
        self.assertIn("repo_search", names)
        self.assertIn("repository_intelligence", names)
        self.assertIn("skill_recall", names)

    def test_ask_mode_stays_lean(self) -> None:
        specs = select_dynamic_tools(
            prompt="What is the UID for SamplePlace?",
            interaction_mode="ask",
            max_tools=6,
        )
        names = {s.name for s in specs}
        self.assertIn("uid_lookup", names)
        self.assertNotIn("email_search", names)
        self.assertTrue(all(s.is_read_only for s in specs))

    def test_ri_category_boosts_data_tools(self) -> None:
        scores = score_tools(
            prompt="coverage",
            interaction_mode="inspect",
            repository_intelligence={
                "profiles": [{"categories": ["data_sources"]}],
                "items": [{"category": "data_sources"}],
            },
        )
        self.assertGreaterEqual(scores.get("sql_lookup", 0), scores.get("jobs_lookup", 0))


class RISkillRecallMidRunTests(unittest.TestCase):
    def test_skill_recall_mid_run(self) -> None:
        feed = ToolRuntimeFeed()
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(max_steps=6, hard_runaway_cap=8),
            feed=feed,
        )
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "tool_request",
                    "tool": "skill_recall",
                    "arguments": {"query": "architecture"},
                    "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
                },
                {
                    "kind": "final_answer",
                    "answer": "Architecture guidance recalled from skills.",
                    "usage": {"input_tokens": 4, "output_tokens": 6, "total_tokens": 10},
                },
            ]
        )
        outcome = runtime.run(
            driver,
            RuntimeContext(
                prompt="Explain project architecture briefly",
                tools_ctx=_ctx(),
                interaction_mode="inspect",
                provider="openai-api",
                model="gpt-test",
                run_id="run-skill-1",
                repository_intelligence={"items": [], "diagnostics": {"knowledge_entries_used": 0}},
            ),
        )
        self.assertEqual(outcome.status, "completed")
        self.assertTrue(any(s.tool == "skill_recall" for s in outcome.steps))
        self.assertIn("skill_recall", outcome.active_tools or [])

    def test_ri_recall_mid_run(self) -> None:
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(max_steps=6, hard_runaway_cap=8),
            feed=ToolRuntimeFeed(),
        )
        ri = MagicMock()
        ri.retrieve.return_value = {
            "profiles": [{"repository_id": "demo", "categories": ["architecture"]}],
            "items": [
                {
                    "repository_id": "demo",
                    "path": "ARCHITECTURE.md",
                    "summary": "Hub adapters only",
                    "category": "architecture",
                }
            ],
            "diagnostics": {"used": True, "knowledge_entries_used": 1},
        }
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "tool_request",
                    "tool": "repository_intelligence",
                    "arguments": {"query": "architecture adapters"},
                },
                {
                    "kind": "final_answer",
                    "answer": "Hub uses adapters only (ARCHITECTURE.md).",
                },
            ]
        )
        outcome = runtime.run(
            driver,
            RuntimeContext(
                prompt="How does the hub talk to repos?",
                tools_ctx=_ctx(repository_intelligence=ri),
                interaction_mode="inspect",
                provider="grok",
                model="grok-test",
                run_id="run-ri-1",
            ),
        )
        self.assertEqual(outcome.status, "completed")
        self.assertTrue(any(s.tool == "repository_intelligence" for s in outcome.steps))
        self.assertGreaterEqual(int(outcome.telemetry.get("ri_entries_used") or 0), 1)


class ContextPruneTests(unittest.TestCase):
    def test_prune_preserves_grounded_facts(self) -> None:
        rows = [
            {
                "tool": "sql_query_execute",
                "ok": True,
                "summary": "count",
                "observation": json.dumps(
                    {"row_count": 1, "rows": [{"n": 42}], "source": "sql"},
                    ensure_ascii=False,
                ),
                "from_t0": True,
            },
            {
                "tool": "repo_search",
                "ok": True,
                "summary": "noise",
                "observation": "x" * 400,
            },
            {
                "tool": "jobs_lookup",
                "ok": True,
                "summary": "old",
                "observation": "y" * 400,
            },
            {
                "tool": "uid_lookup",
                "ok": True,
                "summary": "fresh",
                "observation": json.dumps({"uid": "AbCdEfGhIjK1"}, ensure_ascii=False),
            },
            {
                "tool": "org_unit_lookup",
                "ok": True,
                "summary": "fresh2",
                "observation": json.dumps({"org_units": [{"name": "Sample"}]}, ensure_ascii=False),
            },
        ]
        pruned = prune_observations(
            rows,
            keep=2,
            max_chars=80,
            preserve_grounded=True,
            required_tools={"sql_query_execute"},
        )
        self.assertEqual(len(pruned), 5)
        first = pruned[0]
        self.assertTrue(first.get("elided"))
        self.assertTrue(first.get("preserved_facts") or "facts=" in str(first.get("observation")))
        facts = extract_grounded_facts(rows[0]["observation"])
        self.assertEqual(facts.get("row_count"), 1)


class T0ContinuationTests(unittest.TestCase):
    def test_build_continuation_without_rebuild(self) -> None:
        prior = {
            "evidence_packet": {
                "usable": True,
                "sources": ["tool:sql_lookup"],
                "tool_results": [
                    {
                        "tool": "sql_lookup",
                        "ok": True,
                        "summary": "found query",
                        "query_id": "q1",
                    }
                ],
            },
            "tool_results": [
                {
                    "tool": "sql_lookup",
                    "ok": True,
                    "summary": "found query",
                    "query_id": "q1",
                }
            ],
            "completion_contract": {"intent": "count", "required_output": "number"},
            "t0_failure_reason": "t0_explanation_synthesis",
            "context_fingerprint": "abc123",
        }
        cont = build_continuation_from_t0(prior)
        self.assertTrue(cont.unchanged_context)
        self.assertEqual(cont.context_fingerprint, "abc123")
        self.assertEqual(len(cont.observations), 1)
        self.assertTrue(cont.observations[0].get("from_t0"))

    def test_runtime_seeds_t0_observations(self) -> None:
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(max_steps=4, hard_runaway_cap=6),
            feed=ToolRuntimeFeed(),
        )
        cont = RuntimeContinuation(
            observations=[
                {
                    "tool": "sql_lookup",
                    "ok": True,
                    "summary": "prior",
                    "observation": json.dumps({"query_id": "q1"}),
                    "from_t0": True,
                }
            ],
            completion_contract={"intent": "count", "required_output": "number"},
            unchanged_context=True,
            context_fingerprint=fingerprint_context(prompt="count eligible"),
        )
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "final_answer",
                    "answer": "42 eligible members (from prior sql_lookup).",
                }
            ]
        )
        outcome = runtime.run(
            driver,
            RuntimeContext(
                prompt="count eligible",
                tools_ctx=_ctx(),
                interaction_mode="inspect",
                provider="openai-api",
                model="m1",
                run_id="run-cont-1",
                continuation=cont,
            ),
        )
        self.assertEqual(outcome.status, "completed")
        self.assertTrue(outcome.telemetry.get("continuation_used"))


class SessionReuseTests(unittest.TestCase):
    def test_session_cache_reuses_matching_fingerprint(self) -> None:
        cache = ProviderSessionCache()
        cache.put(
            conversation_id="c1",
            provider="openai-api",
            model="gpt-test",
            previous_response_id="resp_1",
            context_fingerprint="fp-a",
        )
        hit = cache.get(
            conversation_id="c1",
            provider="openai-api",
            model="gpt-test",
            context_fingerprint="fp-a",
        )
        miss = cache.get(
            conversation_id="c1",
            provider="openai-api",
            model="gpt-test",
            context_fingerprint="fp-b",
        )
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit["previous_response_id"], "resp_1")
        self.assertIsNone(miss)


class StuckRecoveryTests(unittest.TestCase):
    def test_soft_recover_then_hard_stuck(self) -> None:
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(
                max_steps=10,
                hard_runaway_cap=12,
                stuck_duplicate_limit=2,
                stuck_max_recoveries=1,
            ),
            feed=ToolRuntimeFeed(),
        )
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "tool_request",
                    "tool": "repository_intelligence",
                    "arguments": {"query": "x"},
                },
                {
                    "kind": "tool_request",
                    "tool": "repository_intelligence",
                    "arguments": {"query": "x"},
                },
                {
                    "kind": "tool_request",
                    "tool": "repository_intelligence",
                    "arguments": {"query": "x"},
                },
                {
                    "kind": "tool_request",
                    "tool": "repository_intelligence",
                    "arguments": {"query": "x"},
                },
            ]
        )
        ri = MagicMock()
        ri.retrieve.return_value = {
            "profiles": [],
            "items": [],
            "diagnostics": {"used": False},
        }
        outcome = runtime.run(
            driver,
            RuntimeContext(
                prompt="lookup x",
                tools_ctx=_ctx(repository_intelligence=ri),
                interaction_mode="inspect",
                provider="grok",
                model="grok-test",
                run_id="run-recover",
            ),
        )
        self.assertEqual(outcome.status, "stuck")
        self.assertEqual(outcome.stop_reason, "duplicate_tool_call")
        self.assertGreaterEqual(outcome.retries, 1)
        self.assertTrue(any(s.summary == "duplicate_recover" for s in outcome.steps))

    def test_stuck_guard_suggests_alternates(self) -> None:
        guard = StuckGuard(duplicate_limit=1, max_recoveries=2)
        guard.note("repo_search", {"q": "a"})
        soft = guard.note("repo_search", {"q": "a"})
        self.assertTrue(soft["recover"])
        self.assertIn("read_file", soft["suggest_tools"])


class CheapestCapableAndManualOverrideTests(unittest.TestCase):
    def test_cheapest_capable_selection(self) -> None:
        prices = {"openai-api": 5.0, "grok": 1.0, "codex": 8.0}

        def avail(p: str) -> tuple[bool, str]:
            return (p in prices, "")

        selection = select_runtime_provider_model(
            manual_override=False,
            selected_provider=None,
            selected_model=None,
            candidates=["openai-api", "grok", "codex"],
            availability=avail,
            price_fn=lambda p: prices[p],
            purpose="synthesis",
        )
        self.assertTrue(selection["ok"])
        self.assertEqual(selection["provider"], "grok")
        self.assertEqual(selection["reason"], "cheapest_capable")

    def test_manual_override_preserved(self) -> None:
        selection = select_runtime_provider_model(
            manual_override=True,
            selected_provider="openai-api",
            selected_model="gpt-4.1-mini",
            candidates=["grok", "openai-api"],
            availability=lambda p: (True, ""),
            price_fn=lambda p: 0.1 if p == "grok" else 9.0,
            purpose="synthesis",
        )
        self.assertTrue(selection["ok"])
        self.assertEqual(selection["provider"], "openai-api")
        self.assertEqual(selection["model"], "gpt-4.1-mini")
        self.assertEqual(selection["reason"], "manual_override")

    def test_manual_override_unavailable_no_silent_fallback(self) -> None:
        selection = select_runtime_provider_model(
            manual_override=True,
            selected_provider="openai-api",
            selected_model="gpt-x",
            candidates=["grok"],
            availability=lambda p: (False, "offline") if p == "openai-api" else (True, ""),
            purpose="synthesis",
        )
        self.assertFalse(selection["ok"])
        self.assertEqual(selection["provider"], "openai-api")
        self.assertIn("No automatic fallback", selection["error"])


class GroundedCompletionAndTelemetryTests(unittest.TestCase):
    def test_grounded_completion_stop(self) -> None:
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(max_steps=6, hard_runaway_cap=8),
            feed=ToolRuntimeFeed(),
        )
        packet = {
            "usable": True,
            "sources": ["tool:sql_query_execute"],
            "hits": [{"tool": "sql_query_execute", "value": 42}],
            "tool_results": [
                {
                    "tool": "sql_query_execute",
                    "ok": True,
                    "summary": "n=42",
                    "row_count": 1,
                    "observation": json.dumps({"rows": [{"n": 42}], "row_count": 1}),
                }
            ],
        }
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "final_answer",
                    "answer": "There are 42 eligible members for SamplePlace 2026 Q2 (sql_query_execute).",
                }
            ]
        )
        outcome = runtime.run(
            driver,
            RuntimeContext(
                prompt="Count eligible members in SamplePlace for 2026 Q2",
                tools_ctx=_ctx(),
                provider="openai-api",
                model="m1",
                interaction_mode="inspect",
                evidence_packet=packet,
                run_id="run-ground-p2",
            ),
        )
        self.assertEqual(outcome.status, "completed")
        self.assertTrue(outcome.telemetry.get("task_solved") or outcome.answer)
        tel = build_runtime_telemetry(
            steps=outcome.steps,
            tool_results=outcome.tool_results,
            context_chars=1200,
            usage=outcome.usage,
            repository_intelligence={"diagnostics": {"knowledge_entries_used": 2}},
            session_reused=True,
            retries=1,
            provider="openai-api",
            model="m1",
            runtime_ms=12.5,
            grounding=outcome.grounding,
            stop_reason=outcome.stop_reason,
            active_tools=outcome.active_tools,
            continuation_used=False,
        )
        for key in (
            "steps",
            "tool_calls",
            "context_chars",
            "ri_entries_used",
            "session_reused",
            "retries",
            "provider",
            "model",
            "runtime_ms",
            "task_solved",
            "grounded",
        ):
            self.assertIn(key, tel)
        self.assertEqual(tel["ri_entries_used"], 2)
        self.assertTrue(tel["session_reused"])
        self.assertEqual(tel["retries"], 1)


if __name__ == "__main__":
    unittest.main()
