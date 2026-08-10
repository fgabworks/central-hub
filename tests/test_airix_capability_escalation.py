"""AiriX capability-aware escalation after T0 — DB before AI, no hard-coded places."""

from __future__ import annotations

import threading
import unittest
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock, patch

from hub.agent_center.capability import (
    FAILURE_NEEDS_REASONING,
    FAILURE_PROVIDER_UNAVAILABLE,
    NEXT_AI,
    NEXT_CANNOT_VERIFY,
    attempt_deterministic_sql,
    classify_t0_failure,
    map_filters_to_sql_params,
    should_escalate_to_ai,
    snapshot_capabilities,
)
from hub.agent_center.completion import derive_completion_contract, validate_completion
from hub.agent_center.routing.classifier import classify_prompt
from hub.agent_center.routing.context import provider_to_adapter_id
from hub.agent_center.routing.models import RouteRecommendation, RoutingSettings
from hub.agent_center.routing.providers import ProviderRegistry
from hub.agent_center.routing.router import recommend_route
from hub.agent_center.routing.telemetry import assert_t0_telemetry_pure, attach_execution_telemetry


def _count_prompt(location: str = "SamplePlace", period: str = "2026 Q2") -> str:
    return f"Count eligible members in {location} for {period}"


@dataclass
class _FakeExecResult:
    ok: bool
    columns: list[str]
    rows: list[list[Any]]
    row_count: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if not self.row_count:
            self.row_count = len(self.rows)


class _FakeSqlStore:
    def __init__(self, queries: list[dict[str, Any]]) -> None:
        self._queries = {str(q["id"]): q for q in queries}

    def list_queries(self, q: str = "", limit: int = 20) -> list[dict[str, Any]]:
        needle = (q or "").lower()
        out = []
        for row in self._queries.values():
            blob = " ".join(
                str(row.get(k) or "") for k in ("title", "description", "sql_text", "tags")
            ).lower()
            if not needle or needle in blob:
                out.append(
                    {
                        "id": row["id"],
                        "title": row.get("title"),
                        "description": row.get("description"),
                        "sql_preview": (row.get("sql_text") or "")[:200],
                    }
                )
            if len(out) >= limit:
                break
        return out

    def get_query(self, query_id: str) -> dict[str, Any] | None:
        return dict(self._queries[query_id]) if query_id in self._queries else None


class _FakeConnections:
    def __init__(self, connection_id: str = "demo-ro", configured: bool = True) -> None:
        self.connection_id = connection_id
        self.configured = configured
        self.profile = MagicMock()
        self.profile.id = connection_id

    def list_public(self) -> list[dict[str, Any]]:
        return [
            {
                "id": self.connection_id,
                "label": "Demo RO",
                "configured": self.configured,
                "enabled": True,
            }
        ]

    def get_configured(self, connection_id: str) -> Any:
        if not self.configured or connection_id != self.connection_id:
            raise LookupError("not configured")
        return self.profile


class _FakeExecutor:
    def __init__(self, result: _FakeExecResult) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    def execute(self, profile: Any, sql: str, **kwargs: Any) -> _FakeExecResult:
        self.calls.append({"sql": sql, "params": kwargs.get("params"), "profile": profile})
        return self.result


class CapabilityUnitTests(unittest.TestCase):
    def test_map_filters_to_params(self) -> None:
        filters = {
            "location": "North",
            "period": ["2026Q2"],
            "population_group": "eligible members",
            "uids": ["A1b2C3d4E5f"],
        }
        bound = map_filters_to_sql_params(
            ["location", "period", "ou_uid"], filters
        )
        assert bound is not None
        self.assertEqual(bound["location"], "North")
        self.assertEqual(bound["period"], "2026Q2")
        self.assertEqual(bound["ou_uid"], "A1b2C3d4E5f")
        self.assertIsNone(map_filters_to_sql_params(["missing_param"], filters))

    def test_connected_db_count_succeeds(self) -> None:
        prompt = _count_prompt()
        contract = derive_completion_contract(prompt)
        store = _FakeSqlStore(
            [
                {
                    "id": "q1",
                    "title": "Eligible count",
                    "description": "count eligible members",
                    "sql_text": "SELECT COUNT(*) AS count FROM members WHERE ou_name = :location AND period = :period",
                    "connection_id": "demo-ro",
                }
            ]
        )
        executor = _FakeExecutor(
            _FakeExecResult(ok=True, columns=["count"], rows=[[42]])
        )
        attempt = attempt_deterministic_sql(
            prompt=prompt,
            contract=contract,
            sql_store=store,
            sql_executor=executor,
            sql_connections=_FakeConnections(),
        )
        self.assertTrue(attempt.ok)
        self.assertTrue(attempt.attempted)
        self.assertIn("42", attempt.answer or "")
        completion = validate_completion(
            contract,
            prompt=prompt,
            answer=attempt.answer or "",
            evidence={
                "usable": True,
                "hits": attempt.hits,
                "sources": ["tool:sql_query_execute"],
                "tool_results": [attempt.tool_result],
            },
        )
        self.assertTrue(completion.task_solved)
        self.assertTrue(completion.answer_grounded)
        self.assertEqual(len(executor.calls), 1)
        self.assertIn("SamplePlace", str(executor.calls[0]["params"].get("location") or ""))

    def test_db_unavailable_clear_failure(self) -> None:
        prompt = _count_prompt()
        contract = derive_completion_contract(prompt)
        attempt = attempt_deterministic_sql(
            prompt=prompt,
            contract=contract,
            sql_store=_FakeSqlStore([]),
            sql_executor=_FakeExecutor(_FakeExecResult(ok=True, columns=["count"], rows=[[1]])),
            sql_connections=_FakeConnections(configured=False),
        )
        self.assertTrue(attempt.unavailable)
        self.assertFalse(attempt.ok)
        caps = snapshot_capabilities(
            sql_store=_FakeSqlStore([]),
            sql_executor=MagicMock(),
            sql_connections=_FakeConnections(configured=False),
            dhis2_reports=None,
        )
        failure = classify_t0_failure(
            contract=contract,
            packet={"usable": False, "hits": [], "sources": []},
            caps=caps,
            sql_attempt=attempt,
            authoritative_data=True,
        )
        self.assertEqual(failure.reason, FAILURE_PROVIDER_UNAVAILABLE)
        self.assertEqual(failure.next_capability, NEXT_CANNOT_VERIFY)
        self.assertFalse(should_escalate_to_ai(failure))

    def test_needs_ai_for_query_construction(self) -> None:
        prompt = _count_prompt(location="Elsewhere")
        contract = derive_completion_contract(prompt)
        store = _FakeSqlStore(
            [
                {
                    "id": "q2",
                    "title": "Complex count TODO",
                    "sql_text": "SELECT COUNT(*) FROM members WHERE TODO",
                    "connection_id": "demo-ro",
                }
            ]
        )
        attempt = attempt_deterministic_sql(
            prompt=prompt,
            contract=contract,
            sql_store=store,
            sql_executor=_FakeExecutor(_FakeExecResult(ok=True, columns=["count"], rows=[[1]])),
            sql_connections=_FakeConnections(),
        )
        self.assertTrue(attempt.needs_ai)
        self.assertFalse(attempt.ok)
        caps = snapshot_capabilities(
            sql_store=store,
            sql_executor=MagicMock(execute=True),
            sql_connections=_FakeConnections(),
            dhis2_reports=None,
            saved_matches=store.list_queries(limit=5),
        )
        # Make executor look available
        caps = snapshot_capabilities(
            sql_store=store,
            sql_executor=_FakeExecutor(_FakeExecResult(ok=True, columns=["c"], rows=[[1]])),
            sql_connections=_FakeConnections(),
            dhis2_reports=None,
            saved_matches=list(store._queries.values()),
        )
        failure = classify_t0_failure(
            contract=contract,
            packet={
                "usable": True,
                "hits": [{"source": "repository", "path": "analytics/count.sql"}],
                "sources": ["tool:repo_search"],
            },
            caps=caps,
            sql_attempt=attempt,
            authoritative_data=True,
        )
        self.assertIn(failure.reason, {FAILURE_NEEDS_REASONING, "filters_or_entity_resolution_incomplete"})
        self.assertEqual(failure.next_capability, NEXT_AI)
        self.assertTrue(should_escalate_to_ai(failure))

    def test_no_wasteful_escalation_when_ai_cannot_help(self) -> None:
        prompt = _count_prompt()
        contract = derive_completion_contract(prompt)
        attempt = attempt_deterministic_sql(
            prompt=prompt,
            contract=contract,
            sql_store=None,
            sql_executor=None,
            sql_connections=None,
        )
        caps = snapshot_capabilities(
            sql_store=None,
            sql_executor=None,
            sql_connections=None,
            dhis2_reports=None,
        )
        failure = classify_t0_failure(
            contract=contract,
            packet={"usable": False, "hits": [], "sources": []},
            caps=caps,
            sql_attempt=attempt,
            authoritative_data=True,
        )
        self.assertFalse(should_escalate_to_ai(failure))
        self.assertEqual(failure.next_capability, NEXT_CANNOT_VERIFY)


class T0DbPathExecutionTests(unittest.TestCase):
    def _executor(self, *, sql_store, sql_executor, sql_connections) -> Any:
        from hub.agent_center.routing.execution import RouteExecutor

        ex = RouteExecutor.__new__(RouteExecutor)
        ex._lock = threading.RLock()
        ex._active = {}
        ex._fingerprints = {}
        fake = MagicMock()
        fake.registry = MagicMock()
        fake.notebook = None
        fake.sql_store = sql_store
        fake.sql_executor = sql_executor
        fake.sql_connections = sql_connections
        fake.uid_index = None
        fake.email = None
        fake.calendar = None
        fake.job_store = None
        fake.audit_store = None
        fake.dhis2_reports = None
        fake.notepad_factory = None
        fake.start_run = MagicMock(side_effect=AssertionError("no AI for this test"))
        ex.agent_center = fake
        ex._tools_context = MagicMock(return_value=MagicMock(
            sql_store=sql_store,
            sql_executor=sql_executor,
            sql_connections=sql_connections,
            repository_ids=["demo"],
        ))
        ex._update = lambda eid, **kw: {**(ex._active.get(eid) or {"id": eid}), **kw}
        ex.get_status = lambda eid: ex._active.get(eid)
        ex._fail = lambda eid, msg, code="": {
            "id": eid,
            "status": "failed",
            "error": msg,
            "error_code": code,
        }
        ex._record_history = MagicMock()
        ex._provider_available = lambda aid: (True, "ok")
        ex._execute_agent = MagicMock(side_effect=AssertionError("must not escalate"))
        return ex

    def test_t0_repo_discovery_then_db_verified_result(self) -> None:
        prompt = _count_prompt(location="WestDistrict")
        c = classify_prompt(prompt, repository_ids=["demo"])
        rec = recommend_route(
            c,
            settings=RoutingSettings(prefer_deterministic=True),
            registry=ProviderRegistry(),
            available_provider_ids={"deterministic", "grok", "codex"},
        )
        store = _FakeSqlStore(
            [
                {
                    "id": "q-west",
                    "title": "WestDistrict eligible",
                    "sql_text": "SELECT 17 AS count",
                    "connection_id": "demo-ro",
                }
            ]
        )
        executor = _FakeExecutor(
            _FakeExecResult(ok=True, columns=["count"], rows=[[17]])
        )
        connections = _FakeConnections()
        ex = self._executor(
            sql_store=store, sql_executor=executor, sql_connections=connections
        )
        packet = {
            "usable": True,
            "hits": [
                {"source": "repository", "path": "sql/eligible_count.sql", "repo_id": "demo"},
                {"source": "sql:saved_query", "query_id": "q-west", "name": "WestDistrict eligible"},
            ],
            "sources": ["tool:repo_search", "tool:sql_lookup"],
            "tool_results": [{"tool": "repo_search", "ok": True}],
            "errors": [],
            "summary": "discovery only",
        }
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value=packet,
        ), patch(
            "hub.agent_center.grounding.answer_from_evidence",
            return_value=None,
        ):
            ex._active["e1"] = {"id": "e1", "status": "queued", "provider_id": "deterministic"}
            out = ex._execute_t0(
                "e1",
                prompt,
                rec,
                {
                    "tool_ids": ["repo_search", "sql_lookup", "org_unit_lookup"],
                    "repository_ids": ["demo"],
                    "evidence_packet": packet,
                },
            )
        self.assertIn("17", out.get("answer") or "")
        g = out.get("grounding") or {}
        self.assertTrue(g.get("task_solved"))
        self.assertTrue(g.get("answer_grounded"))
        self.assertTrue(out.get("db_query_attempted"))
        self.assertFalse(out.get("ai_escalation_occurred"))
        tel = out.get("telemetry") or attach_execution_telemetry(dict(out)).get("telemetry")
        assert_t0_telemetry_pure(tel)
        self.assertTrue(tel.get("db_query_attempted"))
        self.assertFalse(tel.get("ai_escalation_occurred"))
        ex._execute_agent.assert_not_called()

    def test_db_needs_ai_sets_capability_escalate(self) -> None:
        prompt = _count_prompt(location="OpenDistrict")
        c = classify_prompt(prompt, repository_ids=["demo"])
        rec = recommend_route(
            c,
            settings=RoutingSettings(prefer_deterministic=True),
            registry=ProviderRegistry(),
            available_provider_ids={"deterministic", "grok", "codex"},
        )
        store = _FakeSqlStore(
            [
                {
                    "id": "q-open",
                    "title": "Needs bind",
                    "sql_text": "SELECT COUNT(*) AS count FROM t WHERE x = :unmapped_filter",
                    "connection_id": "demo-ro",
                }
            ]
        )
        ex = self._executor(
            sql_store=store,
            sql_executor=_FakeExecutor(_FakeExecResult(ok=True, columns=["count"], rows=[[1]])),
            sql_connections=_FakeConnections(),
        )
        # Allow escalate path to call agent in execute(); here we only test _execute_t0 flag.
        packet = {
            "usable": True,
            "hits": [{"source": "sql:saved_query", "query_id": "q-open"}],
            "sources": ["tool:sql_lookup"],
            "tool_results": [],
            "errors": [],
            "summary": "saved query only",
        }
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value=packet,
        ), patch(
            "hub.agent_center.grounding.answer_from_evidence",
            return_value=None,
        ):
            ex._active["e2"] = {"id": "e2", "status": "queued", "provider_id": "deterministic"}
            out = ex._execute_t0(
                "e2",
                prompt,
                rec,
                {
                    "tool_ids": ["sql_lookup"],
                    "repository_ids": ["demo"],
                    "evidence_packet": packet,
                },
            )
        self.assertTrue(out.get("t0_capability_escalate"))
        self.assertTrue(out.get("t0_unsolved"))
        self.assertEqual(out.get("next_capability"), NEXT_AI)
        self.assertTrue(out.get("db_query_attempted"))


class SelectedCodexEscalationTests(unittest.TestCase):
    def test_selected_codex_model_preserved_on_escalation(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor

        prompt = _count_prompt(location="EscalationDistrict")
        c = classify_prompt(prompt, repository_ids=["demo"])
        rec = recommend_route(
            c,
            settings=RoutingSettings(prefer_deterministic=True),
            registry=ProviderRegistry(),
            available_provider_ids={"deterministic", "codex", "grok"},
        )
        store = _FakeSqlStore(
            [
                {
                    "id": "q-esc",
                    "title": "Needs AI",
                    "sql_text": "SELECT COUNT(*) FROM t WHERE TODO",
                    "connection_id": "demo-ro",
                }
            ]
        )
        fake = MagicMock()
        fake.registry = MagicMock()
        fake.notebook = None
        fake.sql_store = store
        fake.sql_executor = _FakeExecutor(
            _FakeExecResult(ok=True, columns=["count"], rows=[[1]])
        )
        fake.sql_connections = _FakeConnections()
        fake.uid_index = None
        fake.email = None
        fake.calendar = None
        fake.job_store = None
        fake.audit_store = None
        fake.dhis2_reports = None
        fake.notepad_factory = None

        ex = RouteExecutor(fake, availability_loader=lambda: {
            "codex": {"status": "available", "runnable": True},
            "grok": {"status": "available", "runnable": True},
        })
        captured: dict[str, Any] = {}

        def _agent(eid, prompt_n, recommendation, context_preview, **kwargs):
            captured["adapter_id"] = kwargs.get("adapter_id")
            captured["model"] = (context_preview or {}).get("model")
            captured["manual_override"] = kwargs.get("manual_override")
            return {
                "id": eid,
                "status": "completed",
                "answer": "Cannot verify from selected context.\nReason: AI could not derive a verified value.",
                "adapter_id": kwargs.get("adapter_id"),
                "resolved_model": captured["model"],
                "agent_run_id": "child-1",
                "grounding": {
                    "task_solved": False,
                    "answer_grounded": False,
                    "grounded": False,
                    "evidence_found": True,
                },
                "telemetry": {
                    "llm_invoked": True,
                    "provider": "codex",
                    "model": captured["model"],
                    "ai_escalation_occurred": True,
                },
            }

        ex._execute_agent = _agent  # type: ignore[method-assign]
        ex._provider_available = lambda aid: (True, "ok")  # type: ignore[method-assign]

        packet = {
            "usable": True,
            "hits": [{"source": "sql:saved_query", "query_id": "q-esc"}],
            "sources": ["tool:sql_lookup"],
            "tool_results": [],
            "errors": [],
            "summary": "needs construction",
        }
        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value=packet,
        ), patch(
            "hub.agent_center.grounding.answer_from_evidence",
            return_value=None,
        ):
            out = ex.execute(
                prompt=prompt,
                recommendation=rec,
                settings=RoutingSettings(prefer_deterministic=True, require_approval_before_codex=False),
                repository_ids=["demo"],
                agent_override="codex",
                model="gpt-5.3-codex",
                manual_override=True,
                approve_codex=True,
            )
        self.assertEqual(captured.get("adapter_id"), "codex")
        self.assertEqual(captured.get("model"), "gpt-5.3-codex")
        self.assertEqual(provider_to_adapter_id("codex"), "codex")
        self.assertTrue(out.get("ai_escalation_occurred") or captured.get("adapter_id") == "codex")


if __name__ == "__main__":
    unittest.main()
