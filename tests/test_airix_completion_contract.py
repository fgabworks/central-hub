"""AiriX dynamic completion contract — evidence ≠ task solved."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hub.agent_center.completion import (
    INTENT_COMPARISON,
    INTENT_COUNT,
    INTENT_FILE_SEARCH,
    INTENT_LIST,
    INTENT_LOOKUP,
    INTENT_METADATA,
    INTENT_STATUS,
    INTENT_TRACE,
    derive_completion_contract,
    validate_completion,
)
from hub.agent_center.grounding import answer_from_evidence, evaluate_answer_grounding
from hub.agent_center.routing.models import PromptClassification, RouteRecommendation
from hub.agent_center.routing.orchestrate import OrchestrationStep, is_task_solved
from hub.agent_center.routing.telemetry import assert_t0_telemetry_pure, attach_execution_telemetry


def _packet(*, hits=None, sources=None, usable=True, tool="repo_search"):
    hits = list(hits or [])
    sources = list(sources or ([f"tool:{tool}"] if hits or usable else []))
    return {
        "usable": usable,
        "hits": hits,
        "sources": sources,
        "tool_results": [{"tool": tool, "ok": True, "result": {"summary": "ok"}}],
        "errors": [],
        "summary": "evidence",
    }


class DeriveContractTests(unittest.TestCase):
    def test_count_intent(self) -> None:
        c = derive_completion_contract(
            "Count eligible members in an organisation unit for 2026 Q2"
        )
        self.assertEqual(c.intent, INTENT_COUNT)
        self.assertEqual(c.required_output, "verified_numeric_value")
        self.assertTrue(c.filters.get("period") or c.filters.get("entity_types"))

    def test_list_intent(self) -> None:
        c = derive_completion_contract("What are the provinces for Region III?")
        self.assertIn(c.intent, {INTENT_LIST, INTENT_LOOKUP})

    def test_lookup_intent(self) -> None:
        c = derive_completion_contract("Look up the UID for this organisation unit")
        self.assertEqual(c.intent, INTENT_LOOKUP)

    def test_status_intent(self) -> None:
        c = derive_completion_contract("Show the status of approved records for last quarter")
        self.assertEqual(c.intent, INTENT_STATUS)

    def test_metadata_intent(self) -> None:
        c = derive_completion_contract("Show metadata fields for this data element")
        self.assertEqual(c.intent, INTENT_METADATA)

    def test_comparison_intent(self) -> None:
        c = derive_completion_contract("Compare coverage versus prior quarter")
        self.assertEqual(c.intent, INTENT_COMPARISON)

    def test_trace_intent(self) -> None:
        c = derive_completion_contract("Trace where this indicator value comes from")
        self.assertEqual(c.intent, INTENT_TRACE)

    def test_file_search_intent(self) -> None:
        c = derive_completion_contract("Find the file that defines the SQL analytics query")
        self.assertEqual(c.intent, INTENT_FILE_SEARCH)

    def test_explanation_intent_not_list(self) -> None:
        from hub.agent_center.completion import INTENT_EXPLANATION

        c = derive_completion_contract("Explain what a Python list comprehension is")
        self.assertEqual(c.intent, INTENT_EXPLANATION)


class ValidateCompletionTests(unittest.TestCase):
    def test_count_unsolved_when_only_repo_paths(self) -> None:
        contract = derive_completion_contract(
            "Count the number of pregnant women in Brgy. Sample for 2026 Q2"
        )
        packet = _packet(
            hits=[{"source": "repository", "path": "scripts/report.sql", "repo_id": "demo"}],
            tool="repo_search",
        )
        discovery = (
            "Selected-repository matches (read-only). Open these paths for project facts:\n"
            "- demo:scripts/report.sql"
        )
        result = validate_completion(
            contract, prompt="Count ...", answer=discovery, evidence=packet
        )
        self.assertTrue(result.evidence_found)
        self.assertFalse(result.task_solved)
        self.assertFalse(result.answer_grounded)

    def test_count_solved_with_numeric_and_evidence(self) -> None:
        contract = derive_completion_contract("How many households in the municipality?")
        packet = _packet(
            hits=[{"source": "dhis2:analytics", "value": 12}],
            sources=["tool:sql_lookup"],
            tool="sql_lookup",
        )
        result = validate_completion(
            contract,
            prompt="How many households?",
            answer="Count: 12 households",
            evidence=packet,
        )
        self.assertTrue(result.evidence_found)
        self.assertTrue(result.task_solved)
        self.assertTrue(result.answer_grounded)

    def test_list_solved_with_ou_items(self) -> None:
        contract = derive_completion_contract("What are the provinces for Region III?")
        packet = _packet(
            hits=[
                {"source": "dhis2:org_unit_child", "name": "Alpha", "uid": "A1b2C3d4E5f"},
                {"source": "dhis2:org_unit_child", "name": "Beta", "uid": "B1b2C3d4E5f"},
            ],
            sources=["tool:org_unit_lookup"],
            tool="org_unit_lookup",
        )
        answer = (
            "Organisation units from selected DHIS2/project context:\n"
            "- Alpha (A1b2C3d4E5f)\n"
            "- Beta (B1b2C3d4E5f)"
        )
        result = validate_completion(
            contract, prompt="What are the provinces?", answer=answer, evidence=packet
        )
        self.assertTrue(result.task_solved)
        self.assertTrue(result.answer_grounded)

    def test_lookup_status_metadata_file_trace(self) -> None:
        cases = [
            (
                "Look up UID for the organisation unit",
                INTENT_LOOKUP,
                "UID: A1b2C3d4E5f",
                _packet(
                    hits=[{"source": "uid_index", "name": "Sample", "uid": "A1b2C3d4E5f"}],
                    tool="uid_lookup",
                ),
            ),
            (
                "What is the status of this job?",
                INTENT_STATUS,
                "Status: completed",
                _packet(hits=[{"source": "jobs", "status": "completed"}], tool="jobs_lookup"),
            ),
            (
                "Show metadata fields for the indicator",
                INTENT_METADATA,
                "name: Coverage\ncode: COV1",
                _packet(hits=[{"source": "uid_index", "name": "Coverage"}], tool="uid_lookup"),
            ),
            (
                "Find the file for analytics SQL",
                INTENT_FILE_SEARCH,
                "Selected-repository matches (read-only):\n- app/query.sql",
                _packet(
                    hits=[{"source": "repository", "path": "app/query.sql"}],
                    tool="repo_search",
                ),
            ),
            (
                "Trace where this mapping comes from",
                INTENT_TRACE,
                "Selected-repository matches (read-only):\n- hub/mapping.yaml",
                _packet(
                    hits=[{"source": "repository", "path": "hub/mapping.yaml"}],
                    tool="repo_search",
                ),
            ),
        ]
        for prompt, intent, answer, packet in cases:
            with self.subTest(intent=intent):
                contract = derive_completion_contract(prompt)
                self.assertEqual(contract.intent, intent)
                result = validate_completion(
                    contract, prompt=prompt, answer=answer, evidence=packet
                )
                self.assertTrue(result.evidence_found)
                self.assertTrue(result.task_solved, msg=result.reason)
                self.assertTrue(result.answer_grounded)

    def test_comparison_unsolved_without_sides(self) -> None:
        contract = derive_completion_contract("Compare stage versus live coverage")
        packet = _packet(hits=[{"source": "repository", "path": "a.sql"}])
        result = validate_completion(
            contract,
            prompt="Compare stage versus live",
            answer="Selected-repository matches:\n- a.sql",
            evidence=packet,
        )
        self.assertTrue(result.evidence_found)
        self.assertFalse(result.task_solved)


class AnswerFromEvidenceGateTests(unittest.TestCase):
    def test_repo_hits_do_not_answer_count(self) -> None:
        packet = _packet(
            hits=[{"source": "repository", "path": "scripts/x.sql", "repo_id": "demo"}],
            tool="repo_search",
        )
        self.assertIsNone(
            answer_from_evidence(
                "Count pregnant women in Brgy. Sample for 2026 Q2",
                packet,
            )
        )

    def test_ou_list_still_answers_list(self) -> None:
        packet = _packet(
            hits=[
                {"source": "dhis2:org_unit_child", "name": "Alpha", "uid": "A1b2C3d4E5f"},
            ],
            sources=["tool:org_unit_lookup"],
            tool="org_unit_lookup",
        )
        ans = answer_from_evidence("What are the provinces for Region III?", packet)
        self.assertIsNotNone(ans)
        self.assertIn("Alpha", ans or "")


class GroundingFlagsTests(unittest.TestCase):
    def test_discovery_marks_evidence_yes_solved_no(self) -> None:
        packet = _packet(
            hits=[{"source": "repository", "path": "a.sql"}],
            tool="repo_search",
        )
        status = evaluate_answer_grounding(
            "Count households in the municipality for 2026 Q1",
            "Selected-repository matches (read-only):\n- a.sql",
            repository_ids=["demo"],
            evidence=packet,
        )
        self.assertTrue(status.get("evidence_found"))
        self.assertFalse(status.get("task_solved"))
        self.assertFalse(status.get("answer_grounded"))
        self.assertEqual(status.get("grounded_label"), "No")
        self.assertTrue(status.get("cannot_verify"))


class OrchestrationStopTests(unittest.TestCase):
    def test_unsolved_evidence_does_not_stop(self) -> None:
        step = OrchestrationStep(
            id="step_tool_lookup",
            kind="tool",
            label="Tools",
            provider_id="deterministic",
            role_id="analyst",
        )
        self.assertFalse(
            is_task_solved(
                {
                    "status": "completed",
                    "answer": "Selected-repository matches:\n- a.sql",
                    "evidence_packet": {"usable": True},
                    "grounding": {
                        "evidence_found": True,
                        "task_solved": False,
                        "answer_grounded": False,
                        "grounded": False,
                        "cannot_verify": True,
                    },
                },
                step=step,
            )
        )

    def test_fully_solved_stops(self) -> None:
        step = OrchestrationStep(
            id="step_tool_lookup",
            kind="tool",
            label="Tools",
            provider_id="deterministic",
            role_id="analyst",
        )
        self.assertTrue(
            is_task_solved(
                {
                    "status": "completed",
                    "answer": "Count: 3",
                    "evidence_packet": {"usable": True},
                    "grounding": {
                        "evidence_found": True,
                        "task_solved": True,
                        "answer_grounded": True,
                        "grounded": True,
                    },
                },
                step=step,
            )
        )


class T0ExecutionCompletionTests(unittest.TestCase):
    def test_t0_discovery_incomplete_cannot_verify(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor

        fake = MagicMock()
        fake.registry = MagicMock()
        fake.notebook = None
        fake.sql_store = None
        fake.uid_index = None
        fake.email = None
        fake.calendar = None
        fake.job_store = None
        fake.audit_store = None
        fake.dhis2_reports = None
        fake.notepad_factory = None
        fake.start_run = MagicMock(side_effect=AssertionError("no AI"))
        executor = RouteExecutor(fake)
        with executor._lock:
            executor._active["e1"] = {
                "id": "e1",
                "status": "queued",
                "provider_id": "deterministic",
                "adapter_id": None,
                "tier": "T0",
                "task_type": "lookup",
                "prompt": "Count households in Sample for 2026 Q2",
                "workspace": "work",
                "actor": "owner",
            }
        c = PromptClassification(
            task_type="lookup",
            complexity=10,
            risk="low",
            estimated_scope_files=1,
            context_size="small",
            needs_coding=False,
            needs_testing=False,
            needs_architecture=False,
            deterministic_capable=True,
            signals=["deterministic_capable", "authoritative_data_query", "data_query"],
        )
        rec = RouteRecommendation(
            task_type="lookup",
            complexity=10,
            risk="low",
            recommended_agent="deterministic",
            recommended_label="Deterministic",
            recommended_tier="T0",
            alternative_agent="grok",
            alternative_label="Grok",
            confidence=0.8,
            reason="test",
            estimated_usage="Very Low",
            approval_required=False,
            classification=c,
        )
        packet = _packet(
            hits=[{"source": "repository", "path": "sql/count.sql", "repo_id": "demo"}],
            tool="repo_search",
        )
        out = executor._execute_t0(
            "e1",
            "Count households in Sample for 2026 Q2",
            rec,
            {
                "tool_ids": ["repo_search", "org_unit_lookup"],
                "repository_ids": ["demo"],
                "evidence_packet": packet,
            },
        )
        self.assertFalse(out.get("t0_fallthrough"))
        self.assertIn("Cannot verify", out.get("answer") or "")
        g = out.get("grounding") or {}
        self.assertTrue(g.get("evidence_found"))
        self.assertFalse(g.get("task_solved"))
        self.assertFalse(g.get("answer_grounded"))
        tel = out.get("telemetry") or attach_execution_telemetry(dict(out)).get("telemetry")
        assert_t0_telemetry_pure(tel)
        self.assertEqual(tel.get("routing_tier"), "T0")
        self.assertEqual(tel.get("execution_type"), "Deterministic")
        self.assertFalse(tel.get("llm_invoked"))


if __name__ == "__main__":
    unittest.main()
