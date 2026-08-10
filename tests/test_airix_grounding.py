"""AiriX selected-context grounding — no silent general-knowledge fallback."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from hub.agent_center.grounding import (
    answer_from_evidence,
    apply_grounding_to_answer,
    evaluate_answer_grounding,
    format_cannot_verify,
    requires_project_grounding,
)
from hub.agent_center.openai_tools import AgentToolsContext, execute_tool
from hub.agent_center.routing.classifier import classify_prompt
from hub.agent_center.routing.context import select_minimal_tools, select_repository_ids
from hub.agent_center.routing.router import recommend_route
from hub.agent_center.routing.models import RoutingSettings


REGION_III_PROMPT = "What are the provinces for Region III - Central Luzon"


class GroundingRequirementTests(unittest.TestCase):
    def test_pmnp_region_query_requires_grounding_with_repo(self) -> None:
        self.assertTrue(
            requires_project_grounding(REGION_III_PROMPT, repository_ids=["live-processing-local"])
        )

    def test_generic_hello_without_repo_does_not_require(self) -> None:
        self.assertFalse(requires_project_grounding("Hello, what can you do?"))

    def test_explicit_general_knowledge_opt_out(self) -> None:
        self.assertFalse(
            requires_project_grounding(
                "From general knowledge, what are the provinces of Region III?",
                repository_ids=["live-processing-local"],
            )
        )


class ClassifierT0PreferenceTests(unittest.TestCase):
    def test_region_iii_query_is_deterministic_lookup(self) -> None:
        c = classify_prompt(REGION_III_PROMPT, repository_ids=["live-processing-local"])
        self.assertEqual(c.task_type, "lookup")
        self.assertTrue(c.deterministic_capable)
        self.assertIn("project_lookup", c.signals)
        settings = RoutingSettings(prefer_deterministic=True)
        rec = recommend_route(c, settings=settings)
        self.assertEqual(rec.recommended_tier, "T0")
        self.assertEqual(rec.recommended_agent, "deterministic")

    def test_project_lookup_keeps_selected_repo(self) -> None:
        c = classify_prompt(REGION_III_PROMPT, repository_ids=["live-processing-local", "other-repo"])
        ids = select_repository_ids(c, ["live-processing-local", "other-repo"])
        self.assertEqual(ids, ["live-processing-local", "other-repo"][:2])
        tools = select_minimal_tools(c)
        self.assertIn("org_unit_lookup", tools)


class EvidenceAndPolicyTests(unittest.TestCase):
    def test_answer_from_ou_evidence(self) -> None:
        packet = {
            "usable": True,
            "hits": [
                {
                    "source": "dhis2:org_unit_child",
                    "name": "Bulacan",
                    "uid": "aaa",
                },
                {
                    "source": "dhis2:org_unit_child",
                    "name": "Pampanga",
                    "uid": "bbb",
                },
            ],
            "sources": ["tool:org_unit_lookup", "repository:live-processing-local"],
        }
        answer = answer_from_evidence(REGION_III_PROMPT, packet)
        self.assertIsNotNone(answer)
        assert answer is not None
        self.assertIn("Bulacan", answer)
        self.assertIn("Pampanga", answer)
        status = evaluate_answer_grounding(
            REGION_III_PROMPT,
            answer,
            repository_ids=["live-processing-local"],
            evidence=packet,
        )
        self.assertTrue(status["grounded"])
        self.assertEqual(status["grounded_label"], "Yes")

    def test_generic_fallback_blocked_when_grounding_fails(self) -> None:
        fake = (
            "I could not access the repository or DHIS2. Based on general knowledge, "
            "Region III typically includes Aurora, Bataan, Bulacan, Nueva Ecija, "
            "Pampanga, Tarlac, and Zambales."
        )
        status = evaluate_answer_grounding(
            REGION_III_PROMPT,
            fake,
            repository_ids=["live-processing-local"],
            evidence={"usable": False, "hits": [], "sources": [], "summary": "No evidence"},
        )
        self.assertFalse(status["grounded"])
        self.assertTrue(status["policy_violation"])

    def test_missing_evidence_returns_cannot_verify(self) -> None:
        answer = format_cannot_verify(
            repository_ids=["live-processing-local"],
            reason="No usable project evidence found",
        )
        self.assertIn("Cannot verify from selected context", answer)
        status = evaluate_answer_grounding(
            REGION_III_PROMPT,
            answer,
            repository_ids=["live-processing-local"],
            evidence={"usable": False, "hits": [], "sources": [], "summary": "none"},
        )
        self.assertFalse(status["grounded"])
        self.assertTrue(status["cannot_verify"])
        self.assertFalse(status["policy_violation"])
        stamped = apply_grounding_to_answer(answer, status)
        self.assertIn("Grounded: No", stamped)

    def test_no_repo_generic_question_allows_general_knowledge(self) -> None:
        status = evaluate_answer_grounding(
            "Explain what a Python list comprehension is",
            "A list comprehension builds a list from an iterable.",
            repository_ids=[],
            evidence={"usable": False, "hits": [], "sources": []},
        )
        self.assertFalse(status["grounded"])
        self.assertFalse(status["required"])
        self.assertTrue(status.get("task_solved"))
        self.assertFalse(status.get("evidence_found"))
        self.assertEqual(status.get("grounded_label"), "No")


class OrgUnitToolTests(unittest.TestCase):
    def test_org_unit_lookup_tool_wired(self) -> None:
        reports = MagicMock()
        reports.search_org_units.return_value = {
            "org_units": [
                {"id": "reg3", "name": "Region III - Central Luzon", "level": 2},
            ],
            "source": "sqlite",
        }
        # Second call for children
        reports.search_org_units.side_effect = [
            {
                "org_units": [
                    {"id": "reg3", "name": "Region III - Central Luzon", "level": 2},
                ],
                "source": "sqlite",
            },
            {
                "org_units": [
                    {"id": "p1", "name": "Bulacan", "level": 3},
                    {"id": "p2", "name": "ProjectOnlyProvince", "level": 3},
                ],
                "source": "sqlite",
            },
        ]
        ctx = AgentToolsContext(
            registry=MagicMock(),
            repository_ids=["live-processing-local"],
            dhis2_reports=reports,
            allowed_tools={"org_unit_lookup"},
            profile_id="okarun",
            workspace="work",
        )
        raw = execute_tool(
            "org_unit_lookup",
            {"query": "Region III - Central Luzon", "limit": 10, "environment": "stage"},
            ctx,
        )
        data = json.loads(raw)
        self.assertNotIn("error", data)
        self.assertEqual(len(data.get("children") or []), 2)
        self.assertEqual(data["children"][1]["name"], "ProjectOnlyProvince")


class ManualOverrideContextTests(unittest.TestCase):
    def test_manual_override_payload_keeps_repo_and_evidence_keys(self) -> None:
        """Codex manual runs receive repository_ids + evidence via prepare_grounding."""
        from hub.agent_center.service import AgentCenterService

        svc = AgentCenterService.__new__(AgentCenterService)
        svc.registry = MagicMock()
        svc.notebook = None
        svc.sql_store = None
        svc.uid_index = None
        svc.email = None
        svc.calendar = None
        svc.job_store = None
        svc.audit_store = None
        svc.dhis2_reports = MagicMock()
        svc.dhis2_reports.search_org_units.return_value = {
            "org_units": [{"id": "x", "name": "Region III - Central Luzon", "level": 2}],
            "source": "sqlite",
        }
        svc.notepad_factory = None

        prepared = AgentCenterService.prepare_grounding(
            svc,
            REGION_III_PROMPT,
            profile_id="okarun",
            repository_ids=["live-processing-local"],
            tool_ids=["org_unit_lookup", "repo_search", "uid_lookup"],
        )
        self.assertTrue(prepared["required"])
        self.assertIn("live-processing-local", prepared["grounding_rules"])
        self.assertIn("Selected evidence packet", prepared["evidence_packet_text"])


class T0ExecutionGroundingTests(unittest.TestCase):
    def test_t0_uses_project_evidence_without_llm(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor
        from hub.agent_center.routing.models import PromptClassification, RouteRecommendation

        fake = MagicMock()
        fake.registry = MagicMock()
        fake.notebook = None
        fake.sql_store = None
        fake.uid_index = None
        fake.email = None
        fake.calendar = None
        fake.job_store = None
        fake.audit_store = None
        fake.dhis2_reports = MagicMock()
        fake.dhis2_reports.search_org_units.side_effect = [
            {
                "org_units": [
                    {"id": "reg3", "name": "Region III - Central Luzon", "level": 2}
                ],
                "source": "sqlite",
            },
            {
                "org_units": [
                    {"id": "p1", "name": "ProjectProvinceA", "level": 3},
                    {"id": "p2", "name": "ProjectProvinceB", "level": 3},
                ],
                "source": "sqlite",
            },
        ]
        fake.notepad_factory = None

        ex = RouteExecutor(fake)
        classification = classify_prompt(REGION_III_PROMPT)
        rec = RouteRecommendation(
            task_type=classification.task_type,
            complexity=classification.complexity,
            risk=classification.risk,
            recommended_agent="deterministic",
            recommended_label="T0",
            recommended_tier="T0",
            alternative_agent=None,
            alternative_label=None,
            confidence=0.9,
            reason="lookup",
            estimated_usage="Very Low",
            approval_required=False,
            classification=classification,
        )
        with ex._lock:
            ex._active["exec-1"] = {
                "id": "exec-1",
                "status": "queued",
                "provider_id": "deterministic",
            }
        result = ex._execute_t0(
            "exec-1",
            REGION_III_PROMPT,
            rec,
            {
                "tool_ids": ["org_unit_lookup", "uid_lookup"],
                "repository_ids": ["live-processing-local"],
            },
        )
        self.assertEqual(result["status"], "completed")
        self.assertIn("ProjectProvinceA", result.get("answer") or "")
        self.assertTrue((result.get("grounding") or {}).get("grounded"))
        # Must not invent the standard seven-province list without evidence.
        self.assertNotIn("typically includes", (result.get("answer") or "").lower())


if __name__ == "__main__":
    unittest.main()
