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


class QueryConstructionEscalationRuntimeTests(unittest.TestCase):
    """Regression: Baloy-style count → T0 needs query construction → Tool Runtime."""

    def test_t0_query_construction_enters_tool_runtime_not_grounding_gate(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor
        from hub.agent_center.tool_runtime.continuation import build_continuation_from_t0

        prompt = "Count number of pregnant women in Baloy Q2 2026"
        c = classify_prompt(prompt, repository_ids=["live-processing-local"])
        rec = recommend_route(
            c,
            settings=RoutingSettings(prefer_deterministic=True),
            registry=ProviderRegistry(),
            available_provider_ids={"deterministic", "openai-api", "grok"},
        )
        store = _FakeSqlStore(
            [
                {
                    "id": "q-pregnant",
                    "title": "Pregnant women count",
                    "sql_text": "SELECT COUNT(*) FROM pregnant WHERE TODO",
                    "connection_id": "demo-ro",
                }
            ]
        )

        class _Adapter:
            is_api_adapter = True
            descriptor = type("D", (), {"id": "openai-api", "provider": "openai"})()

            def resolve_run_model(self, **_kwargs: Any) -> dict[str, Any]:
                return {"ok": True, "model": "gpt-4.1-mini", "reason": "provider_default"}

            def list_models(self) -> tuple[list[str], str]:
                return (["gpt-4.1-mini"], "test")

        fake = MagicMock()
        fake.registry = MagicMock()
        fake.notebook = None
        fake.sql_store = store
        fake.sql_executor = _FakeExecutor(
            _FakeExecResult(ok=True, columns=["count"], rows=[[17]])
        )
        fake.sql_connections = _FakeConnections()
        fake.uid_index = None
        fake.email = None
        fake.calendar = None
        fake.job_store = None
        fake.audit_store = None
        fake.dhis2_reports = None
        fake.notepad_factory = None
        fake.adapters = [_Adapter()]
        fake.repositories = MagicMock(return_value=[])
        fake.repository_intelligence = MagicMock()

        captured_payload: dict[str, Any] = {}

        def _start_run(payload: dict[str, Any]) -> dict[str, Any]:
            captured_payload.update(payload)
            return {
                "id": "run-baloy-1",
                "status": "completed",
                "agent_id": "openai-api",
                "model": payload.get("model") or "gpt-4.1-mini",
                "answer": (
                    "Count: 17\nSource: read-only SQL (Pregnant women count) via demo-ro"
                ),
                "finished_at": "2026-08-10T00:00:00+00:00",
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 10,
                    "total_tokens": 30,
                    "tool_runtime_steps": [
                        {"tool": "sql_query_execute", "ok": True, "summary": "n=17"}
                    ],
                },
                "context": {
                    "evidence_packet": payload.get("evidence_packet"),
                    "repository_intelligence": payload.get("repository_intelligence"),
                    "packed_prompt_chars": 400,
                    "files": [],
                    "grounding": {
                        "task_solved": True,
                        "answer_grounded": True,
                        "grounded": True,
                        "evidence_found": True,
                        "source": "tool:sql_query_execute",
                    },
                },
                "conversation_id": payload.get("conversation_id") or "",
            }

        fake.start_run = _start_run

        ex = RouteExecutor(
            fake,
            availability_loader=lambda: {
                "openai-api": {"status": "available", "runnable": True},
                "grok": {"status": "available", "runnable": True},
            },
        )
        ex._provider_available = lambda aid: (aid in {"openai-api", "grok"}, "ok")  # type: ignore[method-assign]

        packet = {
            "usable": True,
            "hits": [
                {
                    "source": "repository_intelligence",
                    "path": "docs/pregnant.sql",
                    "summary": "pregnant cohort SQL",
                },
                {"source": "sql:saved_query", "query_id": "q-pregnant"},
            ],
            "sources": [
                "tool:repo_search",
                "tool:sql_lookup",
                "tool:repository_intelligence",
            ],
            "tool_results": [
                {"tool": "repo_search", "ok": True},
                {"tool": "sql_lookup", "ok": True},
                {"tool": "repository_intelligence", "ok": True},
            ],
            "errors": [],
            "summary": "source available; query construction needed",
        }
        contract = derive_completion_contract(
            prompt, classification_signals=list(c.signals or [])
        )
        prior = {
            "evidence_packet": packet,
            "tool_results": packet["tool_results"],
            "completion_contract": contract.public(),
            "detected_filters": dict(contract.filters),
            "t0_failure_reason": FAILURE_NEEDS_REASONING,
            "next_capability": NEXT_AI,
            "repository_intelligence": {
                "profiles": [{"repository_id": "live-processing-local"}],
                "items": [{"path": "docs/pregnant.sql", "summary": "cohort"}],
                "diagnostics": {
                    "used": True,
                    "knowledge_entries_used": 6,
                    "repository_ids": ["live-processing-local"],
                    "freshness": "current",
                },
            },
        }
        continuation = build_continuation_from_t0(prior)
        esc_context = {
            "tool_ids": ["repo_search", "sql_lookup", "repository_intelligence"],
            "repository_ids": ["live-processing-local"],
            "evidence_packet": packet,
            "repository_intelligence": prior["repository_intelligence"],
            "completion_contract": contract.public(),
            "detected_filters": dict(contract.filters),
            "t0_failure_reason": FAILURE_NEEDS_REASONING,
            "next_capability": NEXT_AI,
            "t0_capability_escalate": True,
            "t0_continuation": continuation.public(),
            "tool_runtime_lean_context": True,
            "interaction_mode": "inspect",
            "context_sources": ["ro_database", "files"],
        }
        ex._active["e-baloy"] = {
            "id": "e-baloy",
            "status": "running",
            "fallback_from": "deterministic",
            "fallback_reason": FAILURE_NEEDS_REASONING,
            "t0_failure_reason": FAILURE_NEEDS_REASONING,
            "next_capability": NEXT_AI,
            "ai_escalation_occurred": True,
            "completion_contract": contract.public(),
            "detected_filters": dict(contract.filters),
            "adapter_id": "openai-api",
        }

        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value={
                "usable": False,
                "hits": [],
                "sources": [],
                "tool_results": [],
                "errors": ["rebuild would wipe T0"],
                "summary": "empty rebuild",
            },
        ) as rebuild:
            out = ex._execute_agent(
                "e-baloy",
                prompt,
                rec,
                esc_context,
                adapter_id="openai-api",
                repository_ids=["live-processing-local"],
                settings=RoutingSettings(prefer_deterministic=True),
            )

        # Must not stop at grounding_gate with Model None / Cannot verify.
        self.assertNotEqual(out.get("mode"), "grounding_gate")
        self.assertTrue(captured_payload, "start_run must be called (Tool Runtime entry)")
        # Rebuild must not replace prior usable T0 evidence.
        rebuild.assert_not_called()
        self.assertTrue(captured_payload.get("tool_runtime"))
        self.assertIn("sql_query_execute", captured_payload.get("tool_ids") or [])
        self.assertTrue(captured_payload.get("model"))
        self.assertEqual(captured_payload.get("agent_id"), "openai-api")
        esc_packet = captured_payload.get("evidence_packet") or {}
        self.assertTrue(esc_packet.get("usable"))
        self.assertIn("tool:repository_intelligence", esc_packet.get("sources") or [])
        self.assertTrue(
            captured_payload.get("t0_continuation") or captured_payload.get("reuse_context")
        )
        self.assertEqual(
            (captured_payload.get("completion_contract") or {}).get("intent"),
            "count",
        )
        self.assertIn("17", out.get("answer") or "")
        g = out.get("grounding") or {}
        self.assertTrue(
            g.get("task_solved")
            or g.get("answer_grounded")
            or "17" in (out.get("answer") or "")
        )
        self.assertTrue(out.get("ai_escalation_occurred"))
        self.assertEqual(
            out.get("resolved_model") or out.get("model") or captured_payload.get("model"),
            "gpt-4.1-mini",
        )


if __name__ == "__main__":
    unittest.main()
