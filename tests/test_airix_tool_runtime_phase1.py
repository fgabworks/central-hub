"""AiriX Unified Tool Runtime — Phase 1 focused tests."""

from __future__ import annotations

import json
import unittest
from typing import Any
from unittest.mock import MagicMock

from hub.agent_center.openai_tools import AgentToolsContext
from hub.agent_center.tool_runtime.executor import UnifiedToolExecutor
from hub.agent_center.tool_runtime.feed import ToolRuntimeFeed
from hub.agent_center.tool_runtime.policy import (
    policy_gate,
    select_active_tools,
    tool_runtime_needed,
)
from hub.agent_center.tool_runtime.prune import cap_observation, prune_observations
from hub.agent_center.tool_runtime.runtime import RuntimeContext, ScriptedModelDriver, ToolRuntime
from hub.agent_center.tool_runtime.settings import ToolRuntimeSettings
from hub.agent_center.tool_runtime.specs import TOOL_SPECS, get_tool_spec
from hub.agent_center.tool_runtime.stuck import StuckGuard
from hub.registry.models import Registry


def _ctx(**kwargs: Any) -> AgentToolsContext:
    return AgentToolsContext(
        registry=Registry([]),
        repository_ids=list(kwargs.pop("repository_ids", [])),
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
                    "data_explorer_lookup",
                    "jobs_lookup",
                },
            )
        ),
        **kwargs,
    )


class ToolSpecRegistryTests(unittest.TestCase):
    def test_phase1_core_tools_are_read_only(self) -> None:
        for name in (
            "repo_search",
            "read_file",
            "repository_intelligence",
            "uid_lookup",
            "org_unit_lookup",
            "sql_lookup",
            "sql_query_execute",
            "dhis2_reports_lookup",
            "data_explorer_lookup",
            "jobs_lookup",
        ):
            spec = get_tool_spec(name)
            self.assertIsNotNone(spec)
            assert spec is not None
            self.assertTrue(spec.is_read_only)
            self.assertFalse(spec.requires_approval)

    def test_no_write_tools_in_registry(self) -> None:
        for spec in TOOL_SPECS.values():
            self.assertEqual(spec.access, "read")


class ActiveToolFilteringTests(unittest.TestCase):
    def test_ask_mode_minimal_ro_subset(self) -> None:
        specs = select_active_tools(interaction_mode="ask", max_tools=10)
        names = {s.name for s in specs}
        self.assertIn("uid_lookup", names)
        self.assertNotIn("email_search", names)
        self.assertTrue(all(s.is_read_only for s in specs))

    def test_inspect_includes_repo_and_sql(self) -> None:
        specs = select_active_tools(interaction_mode="inspect", max_tools=12)
        names = {s.name for s in specs}
        self.assertIn("repo_search", names)
        self.assertIn("sql_query_execute", names)
        self.assertIn("repository_intelligence", names)

    def test_context_sources_add_data_explorer(self) -> None:
        specs = select_active_tools(
            interaction_mode="ask",
            context_sources=["data_explorer"],
            max_tools=12,
        )
        names = {s.name for s in specs}
        self.assertIn("data_explorer_lookup", names)

    def test_rbac_filters_sql(self) -> None:
        specs = select_active_tools(
            interaction_mode="inspect",
            permissions={"tools.repository", "tools.dhis2"},
            max_tools=12,
        )
        names = {s.name for s in specs}
        self.assertNotIn("sql_lookup", names)
        self.assertNotIn("sql_query_execute", names)
        self.assertIn("repo_search", names)


class SqlRoSafetyTests(unittest.TestCase):
    def test_free_form_sql_not_accepted_without_query_id(self) -> None:
        executor = UnifiedToolExecutor()
        ctx = _ctx(sql_store=MagicMock(), sql_executor=MagicMock(), sql_connections=MagicMock())
        result = executor.execute(
            "sql_query_execute",
            {"sql": "DELETE FROM users"},
            ctx,
            interaction_mode="inspect",
            active_names={"sql_query_execute"},
        )
        self.assertFalse(result.ok)
        self.assertIn("query_id", result.error.lower() + result.summary.lower())

    def test_write_sql_rejected_by_policy(self) -> None:
        store = MagicMock()
        store.get_query.return_value = {
            "id": "q1",
            "title": "bad",
            "sql_text": "DELETE FROM members WHERE id = 1",
            "connection_id": "demo-ro",
        }
        executor = UnifiedToolExecutor()
        ctx = _ctx(
            sql_store=store,
            sql_executor=MagicMock(),
            sql_connections=MagicMock(),
        )
        result = executor.execute(
            "sql_query_execute",
            {"query_id": "q1"},
            ctx,
            interaction_mode="inspect",
            active_names={"sql_query_execute"},
        )
        self.assertFalse(result.ok)
        self.assertIn("read-only", (result.error or result.summary).lower())

    def test_saved_select_executes_via_ro_executor(self) -> None:
        store = MagicMock()
        store.get_query.return_value = {
            "id": "q1",
            "title": "count",
            "sql_text": "SELECT COUNT(*) AS n FROM members",
            "connection_id": "demo-ro",
        }
        connections = MagicMock()
        connections.get_configured.return_value = MagicMock()
        sql_exec = MagicMock()
        sql_exec.execute.return_value = MagicMock(
            ok=True, columns=["n"], rows=[[42]], row_count=1, error=None
        )
        executor = UnifiedToolExecutor()
        ctx = _ctx(
            sql_store=store,
            sql_executor=sql_exec,
            sql_connections=connections,
        )
        result = executor.execute(
            "sql_query_execute",
            {"query_id": "q1"},
            ctx,
            interaction_mode="inspect",
            active_names={"sql_query_execute"},
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.raw.get("rows"), [[42]])
        sql_exec.execute.assert_called_once()


class Dhis2RoTests(unittest.TestCase):
    def test_org_unit_lookup_uses_dhis2_reports_ro(self) -> None:
        reports = MagicMock()
        reports.search_org_units.return_value = {
            "items": [{"id": "abc", "name": "Region III"}],
            "source": "dhis2_reports",
        }
        executor = UnifiedToolExecutor()
        ctx = _ctx(dhis2_reports=reports, dhis2_environment="stage")
        result = executor.execute(
            "org_unit_lookup",
            {"query": "Region III", "limit": 5},
            ctx,
            interaction_mode="inspect",
            active_names={"org_unit_lookup"},
        )
        self.assertTrue(result.ok)
        reports.search_org_units.assert_called()
        # First positional arg is environment (stage/live isolation).
        args = reports.search_org_units.call_args.args
        self.assertTrue(args)
        self.assertIn(args[0], {"stage", "live"})


class MultiStepRuntimeTests(unittest.TestCase):
    def test_multi_step_tool_use_and_completion_stop(self) -> None:
        ri = MagicMock()
        ri.retrieve.return_value = {
            "profiles": [{"repository_id": "demo", "status": "current", "compact_summary": "demo"}],
            "items": [
                {
                    "repository_id": "demo",
                    "path": "README.md",
                    "title": "Readme",
                    "summary": "Demo project facts for grounding.",
                    "score": 3,
                }
            ],
            "diagnostics": {"used": True, "knowledge_entries_used": 1},
        }
        feed = ToolRuntimeFeed()
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(max_steps=6, hard_runaway_cap=8, stuck_duplicate_limit=2),
            feed=feed,
        )
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "tool_request",
                    "tool": "repository_intelligence",
                    "arguments": {"query": "demo facts", "limit": 3},
                    "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                },
                    {
                    "kind": "tool_request",
                    "tool": "jobs_lookup",
                    "arguments": {"limit": 5},
                    "usage": {"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
                },
                {
                    "kind": "final_answer",
                    "answer": (
                        "The selected repository README.md describes demo project facts "
                        "for grounding. Source: repository_intelligence:demo:README.md"
                    ),
                    "usage": {"input_tokens": 12, "output_tokens": 20, "total_tokens": 32},
                },
            ]
        )
        jobs = MagicMock()
        jobs.list_recent.return_value = []
        ctx = RuntimeContext(
            prompt="Explain what the selected repository README says about demo project facts",
            tools_ctx=_ctx(
                repository_ids=["demo"],
                repository_intelligence=ri,
                job_store=jobs,
            ),
            interaction_mode="inspect",
            provider="openai-api",
            model="gpt-test",
            run_id="run-multi-1",
            evidence_packet={"usable": False, "sources": [], "hits": [], "tool_results": []},
        )
        outcome = runtime.run(driver, ctx)
        self.assertEqual(outcome.provider, "openai-api")
        self.assertEqual(outcome.model, "gpt-test")
        self.assertGreaterEqual(len(outcome.steps), 2)
        self.assertTrue(any(s.tool == "repository_intelligence" for s in outcome.steps))
        snap = feed.snapshot("run-multi-1")
        self.assertGreaterEqual(snap["step_count"], 2)
        self.assertEqual(outcome.usage.get("total_tokens"), 15 + 12 + 32)

    def test_duplicate_guard_stops_runaway(self) -> None:
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(
                max_steps=10,
                hard_runaway_cap=12,
                stuck_duplicate_limit=2,
                stuck_max_recoveries=0,
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
                tools_ctx=_ctx(repository_ids=["demo"], repository_intelligence=ri),
                interaction_mode="inspect",
                provider="grok",
                model="grok-test",
                run_id="run-dup",
            ),
        )
        self.assertEqual(outcome.status, "stuck")
        self.assertEqual(outcome.stop_reason, "duplicate_tool_call")

    def test_timeout_and_cancel(self) -> None:
        runtime = ToolRuntime(
            settings=ToolRuntimeSettings(timeout_seconds=0.01, max_steps=5, hard_runaway_cap=5),
            feed=ToolRuntimeFeed(),
        )

        class SlowDriver:
            def step(self, **_: Any) -> dict[str, Any]:
                import time

                time.sleep(0.05)
                return {"kind": "final_answer", "answer": "late"}

        outcome = runtime.run(
            SlowDriver(),
            RuntimeContext(
                prompt="anything",
                tools_ctx=_ctx(),
                interaction_mode="ask",
                provider="openai-api",
                model="m1",
                run_id="run-timeout",
            ),
        )
        self.assertEqual(outcome.status, "timed_out")

        cancelled = {"v": False}

        class CancelDriver:
            def step(self, **_: Any) -> dict[str, Any]:
                cancelled["v"] = True
                return {"kind": "tool_request", "tool": "jobs_lookup", "arguments": {}}

        runtime2 = ToolRuntime(
            settings=ToolRuntimeSettings(max_steps=5, hard_runaway_cap=5),
            feed=ToolRuntimeFeed(),
        )
        outcome2 = runtime2.run(
            CancelDriver(),
            RuntimeContext(
                prompt="cancel me",
                tools_ctx=_ctx(job_store=MagicMock()),
                interaction_mode="ask",
                provider="openai-api",
                model="m1",
                run_id="run-cancel",
                cancel_check=lambda: True,
            ),
        )
        self.assertEqual(outcome2.status, "cancelled")

    def test_exact_provider_model_preservation(self) -> None:
        runtime = ToolRuntime(feed=ToolRuntimeFeed())
        missing = runtime.run(
            ScriptedModelDriver([{"kind": "final_answer", "answer": "x"}]),
            RuntimeContext(
                prompt="hi",
                tools_ctx=_ctx(),
                provider="",
                model="m1",
                interaction_mode="agent",
            ),
        )
        self.assertEqual(missing.status, "failed")
        self.assertIn("exact provider and model", missing.error.lower())

        class SwapDriver:
            def step(self, **kwargs: Any) -> dict[str, Any]:
                return {
                    "kind": "final_answer",
                    "answer": "nope",
                    "model": "other-model",
                }

        swapped = runtime.run(
            SwapDriver(),
            RuntimeContext(
                prompt="hi",
                tools_ctx=_ctx(),
                provider="openai-api",
                model="fixed-model",
                interaction_mode="agent",
                run_id="run-swap",
            ),
        )
        self.assertEqual(swapped.status, "failed")
        self.assertEqual(swapped.stop_reason, "model_mismatch")
        self.assertEqual(swapped.model, "fixed-model")

    def test_grounding_on_completion_stop(self) -> None:
        runtime = ToolRuntime(feed=ToolRuntimeFeed())
        driver = ScriptedModelDriver(
            [
                {
                    "kind": "final_answer",
                    "answer": (
                        "Count is 12 eligible members for SamplePlace in 2026 Q2 "
                        "from saved SQL query demo."
                    ),
                }
            ]
        )
        packet = {
            "usable": True,
            "sources": ["tool:sql_query_execute"],
            "hits": [{"path": "sql:q1", "snippet": "12"}],
            "tool_results": [
                {"tool": "sql_query_execute", "ok": True, "summary": "12 rows"}
            ],
        }
        outcome = runtime.run(
            driver,
            RuntimeContext(
                prompt="Count eligible members in SamplePlace for 2026 Q2",
                tools_ctx=_ctx(),
                provider="openai-api",
                model="m1",
                interaction_mode="inspect",
                evidence_packet=packet,
                run_id="run-ground",
            ),
        )
        self.assertEqual(outcome.status, "completed")
        self.assertTrue(outcome.grounding.get("task_solved") or outcome.answer)


class StuckAndPruneTests(unittest.TestCase):
    def test_stuck_guard(self) -> None:
        guard = StuckGuard(duplicate_limit=2, max_recoveries=0)
        self.assertFalse(guard.note("a", {"q": 1})["blocked"])
        self.assertFalse(guard.note("a", {"q": 1})["blocked"])
        self.assertTrue(guard.note("a", {"q": 1})["blocked"])

    def test_stuck_guard_recovers_before_hard_stop(self) -> None:
        guard = StuckGuard(duplicate_limit=2, max_recoveries=1)
        self.assertFalse(guard.note("a", {"q": 1})["blocked"])
        self.assertFalse(guard.note("a", {"q": 1})["blocked"])
        soft = guard.note("a", {"q": 1})
        self.assertTrue(soft["recover"])
        self.assertFalse(soft["blocked"])
        hard = guard.note("a", {"q": 1})
        self.assertTrue(hard["blocked"])
        self.assertFalse(hard["recover"])

    def test_prune_observations(self) -> None:
        rows = [
            {"tool": f"t{i}", "ok": True, "summary": "s", "observation": "x" * 100}
            for i in range(6)
        ]
        pruned = prune_observations(rows, keep=2, max_chars=50)
        self.assertEqual(len(pruned), 6)
        self.assertTrue(pruned[0].get("elided"))
        self.assertIn("truncated", cap_observation("y" * 200, max_chars=40))


class PolicyAndNeedTests(unittest.TestCase):
    def test_write_blocked(self) -> None:
        gate = policy_gate("repo_search", interaction_mode="inspect", active_names={"repo_search"})
        self.assertTrue(gate["allowed"])

    def test_tool_runtime_needed_modes(self) -> None:
        self.assertTrue(
            tool_runtime_needed(interaction_mode="inspect", adapter_is_api=True)
        )
        self.assertTrue(
            tool_runtime_needed(interaction_mode="ask", adapter_is_api=True)
        )
        self.assertTrue(
            tool_runtime_needed(interaction_mode="agent", adapter_is_api=True)
        )
        self.assertFalse(
            tool_runtime_needed(interaction_mode="inspect", adapter_is_api=False)
        )
        self.assertFalse(
            tool_runtime_needed(
                interaction_mode="inspect", adapter_is_api=True, t0_solved=True
            )
        )

    def test_policy_blocks_inactive_tool(self) -> None:
        gate = policy_gate(
            "sql_query_execute",
            interaction_mode="ask",
            active_names={"uid_lookup"},
        )
        self.assertFalse(gate["allowed"])


class TelemetryFeedTests(unittest.TestCase):
    def test_feed_records_steps(self) -> None:
        feed = ToolRuntimeFeed()
        feed.reset("r1")
        from hub.agent_center.tool_runtime.results import ToolStepRecord

        feed.append(
            "r1",
            ToolStepRecord(
                step=1,
                provider="openai-api",
                model="m",
                tool="repo_search",
                ok=True,
                summary="2 matches",
                duration_ms=12.5,
                result="ok",
                context_chars=100,
                total_tokens=20,
            ),
        )
        snap = feed.snapshot("r1")
        self.assertEqual(snap["step_count"], 1)
        self.assertEqual(snap["steps"][0]["tool"], "repo_search")
        self.assertEqual(snap["steps"][0]["tokens"], 20)

    def test_public_execution_attaches_feed(self) -> None:
        from hub.agent_center.routing.lifecycle import public_execution_fields
        from hub.agent_center.tool_runtime.feed import GLOBAL_TOOL_RUNTIME_FEED
        from hub.agent_center.tool_runtime.results import ToolStepRecord

        GLOBAL_TOOL_RUNTIME_FEED.reset("child-run")
        GLOBAL_TOOL_RUNTIME_FEED.append(
            "child-run",
            ToolStepRecord(
                step=1,
                provider="grok",
                model="g",
                tool="uid_lookup",
                ok=True,
                summary="hit",
                duration_ms=3,
            ),
        )
        row = public_execution_fields(
            {
                "id": "exec1",
                "status": "running",
                "agent_run_id": "child-run",
                "adapter_id": "grok",
                "mode": "ask",
            }
        )
        self.assertIn("tool_runtime_feed", row)
        self.assertEqual(row["tool_runtime_feed"]["steps"][0]["tool"], "uid_lookup")


class RepositoryIntelligenceToolTests(unittest.TestCase):
    def test_repository_intelligence_handler(self) -> None:
        svc = MagicMock()
        svc.retrieve.return_value = {
            "profiles": [{"repository_id": "demo", "status": "current"}],
            "items": [{"repository_id": "demo", "path": "a.py", "summary": "x", "score": 1}],
            "diagnostics": {"used": True},
        }
        executor = UnifiedToolExecutor()
        result = executor.execute(
            "repository_intelligence",
            {"query": "a", "limit": 3},
            _ctx(repository_ids=["demo"], repository_intelligence=svc),
            interaction_mode="inspect",
            active_names={"repository_intelligence"},
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.raw["items"][0]["path"], "a.py")


if __name__ == "__main__":
    unittest.main()
