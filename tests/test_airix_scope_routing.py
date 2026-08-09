"""AiriX dynamic scope detection + general-knowledge routing."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from hub.agent_center.grounding import (
    answer_from_evidence,
    dedupe_evidence_hits,
    evaluate_answer_grounding,
    format_cannot_verify,
    requires_project_grounding,
)
from hub.agent_center.routing.classifier import classify_prompt
from hub.agent_center.routing.context import select_repository_ids
from hub.agent_center.routing.findings import select_relevant_findings
from hub.agent_center.routing.models import PromptClassification, RoutingSettings
from hub.agent_center.routing.router import recommend_route
from hub.agent_center.scope import (
    SCOPE_AMBIGUOUS,
    SCOPE_GK,
    SCOPE_NATIONAL,
    SCOPE_PROJECT,
    detect_prompt_scope,
    scopes_compatible,
)


REGION_III = "What are the provinces for Region III - Central Luzon"
NATIONAL_OVERRIDE = (
    "From general knowledge / nationally, what are the provinces of Region III?"
)
SIMPLE_GK = "Explain what a Python list comprehension is"


class ScopeDetectionTests(unittest.TestCase):
    def test_ambiguous_prompt_with_selected_repo_is_project_authoritative(self) -> None:
        scope = detect_prompt_scope(REGION_III, repository_ids=["live-processing-local"])
        self.assertEqual(scope.kind, SCOPE_AMBIGUOUS)
        self.assertTrue(scope.requires_project_evidence)
        self.assertTrue(scope.use_selected_repo)
        self.assertFalse(scope.allow_general_knowledge)
        self.assertTrue(
            requires_project_grounding(REGION_III, repository_ids=["live-processing-local"])
        )

    def test_explicit_national_general_overrides_selected_repo(self) -> None:
        scope = detect_prompt_scope(
            NATIONAL_OVERRIDE, repository_ids=["live-processing-local"]
        )
        self.assertIn(scope.kind, {SCOPE_NATIONAL, SCOPE_GK})
        self.assertFalse(scope.requires_project_evidence)
        self.assertTrue(scope.allow_general_knowledge)
        self.assertFalse(scope.use_selected_repo)
        self.assertFalse(
            requires_project_grounding(
                NATIONAL_OVERRIDE, repository_ids=["live-processing-local"]
            )
        )

    def test_simple_general_knowledge_scope(self) -> None:
        scope = detect_prompt_scope(SIMPLE_GK, repository_ids=["live-processing-local"])
        self.assertEqual(scope.kind, SCOPE_GK)
        self.assertFalse(scope.requires_project_evidence)
        self.assertTrue(scope.allow_general_knowledge)

    def test_explicit_project_scope(self) -> None:
        scope = detect_prompt_scope(
            "In this project, what are the provinces for Region III?",
            repository_ids=[],
        )
        self.assertEqual(scope.kind, SCOPE_PROJECT)
        self.assertTrue(scope.requires_project_evidence)


class RoutingByScopeTests(unittest.TestCase):
    def test_simple_gk_routes_to_lowest_tier_not_codex(self) -> None:
        c = classify_prompt(SIMPLE_GK, repository_ids=["live-processing-local"])
        self.assertIn("simple_general_knowledge", c.signals)
        self.assertFalse(c.deterministic_capable)
        rec = recommend_route(c, settings=RoutingSettings(prefer_deterministic=True))
        self.assertEqual(rec.recommended_tier, "T1")
        self.assertNotEqual(rec.recommended_agent, "codex")
        self.assertNotEqual(rec.recommended_agent, "deterministic")

    def test_ambiguous_region_with_repo_prefers_t0(self) -> None:
        c = classify_prompt(REGION_III, repository_ids=["live-processing-local"])
        self.assertIn("project_grounding_required", c.signals)
        self.assertTrue(c.deterministic_capable)
        rec = recommend_route(c, settings=RoutingSettings(prefer_deterministic=True))
        self.assertEqual(rec.recommended_tier, "T0")
        self.assertEqual(rec.recommended_agent, "deterministic")

    def test_national_override_does_not_keep_repo_in_context(self) -> None:
        c = classify_prompt(NATIONAL_OVERRIDE, repository_ids=["live-processing-local"])
        self.assertNotIn("project_grounding_required", c.signals)
        ids = select_repository_ids(c, ["live-processing-local", "other"])
        self.assertEqual(ids, [])

    def test_coding_still_can_route_codex(self) -> None:
        prompt = (
            "Refactor the entire authentication module architecture across 12 files "
            "and redesign the cross-module boundaries"
        )
        c = classify_prompt(prompt)
        rec = recommend_route(c, settings=RoutingSettings())
        self.assertEqual(rec.recommended_tier, "T3")
        self.assertEqual(rec.recommended_agent, "codex")


class T0FallbackPolicyTests(unittest.TestCase):
    def test_t0_success_grounded(self) -> None:
        packet = {
            "usable": True,
            "hits": [
                {"source": "dhis2:org_unit_child", "name": "Bulacan", "uid": "aaa"},
                {"source": "dhis2:org_unit_child", "name": "Pampanga", "uid": "bbb"},
            ],
            "sources": ["tool:org_unit_lookup"],
        }
        answer = answer_from_evidence(REGION_III, packet)
        self.assertIsNotNone(answer)
        status = evaluate_answer_grounding(
            REGION_III,
            answer or "",
            repository_ids=["live-processing-local"],
            evidence=packet,
        )
        self.assertTrue(status["grounded"])
        self.assertEqual(status["grounded_label"], "Yes")

    def test_t0_miss_project_scope_stops(self) -> None:
        answer = format_cannot_verify(
            repository_ids=["live-processing-local"],
            reason="No usable project evidence found",
        )
        status = evaluate_answer_grounding(
            REGION_III,
            answer,
            repository_ids=["live-processing-local"],
            evidence={"usable": False, "hits": [], "sources": []},
        )
        self.assertTrue(status["cannot_verify"])
        self.assertTrue(status["required"])
        self.assertFalse(status["policy_violation"])

    def test_t0_miss_general_scope_allows_model_knowledge(self) -> None:
        status = evaluate_answer_grounding(
            NATIONAL_OVERRIDE,
            "Region III provinces typically include Bulacan, Pampanga, …",
            repository_ids=["live-processing-local"],
            evidence={"usable": False, "hits": [], "sources": []},
        )
        self.assertFalse(status["required"])
        self.assertTrue(status["grounded"])
        self.assertIn("general", status["source"].lower())


class ScopeChangeAndDedupeTests(unittest.TestCase):
    def test_scope_change_drops_irrelevant_prior_findings(self) -> None:
        self.assertFalse(scopes_compatible(SCOPE_NATIONAL, SCOPE_PROJECT))
        self.assertFalse(scopes_compatible(SCOPE_PROJECT, SCOPE_GK))
        self.assertTrue(scopes_compatible(SCOPE_PROJECT, SCOPE_AMBIGUOUS))

        classification = PromptClassification(
            task_type="general",
            complexity=12,
            risk="low",
            estimated_scope_files=1,
            context_size="small",
            needs_coding=False,
            needs_testing=False,
            needs_architecture=False,
            deterministic_capable=False,
            signals=["scope:national_general", "allow_general_knowledge"],
        )
        priors = [
            {
                "task_type": "lookup",
                "summary": "Project OU list for Region III from PMNP mapping",
                "keywords": ["region", "provinces", "pmnp", "project"],
                "grounding_scope": SCOPE_PROJECT,
            },
            {
                "task_type": "general",
                "summary": "National Region III geography overview for Philippines",
                "keywords": ["region", "provinces", "national", "philippines"],
                "grounding_scope": SCOPE_NATIONAL,
            },
        ]
        kept = select_relevant_findings(
            priors,
            prompt=NATIONAL_OVERRIDE,
            classification=classification,
            max_items=3,
            min_score=0.5,
        )
        scopes = {str(r.get("grounding_scope") or "") for r in kept}
        self.assertNotIn(SCOPE_PROJECT, scopes)
        self.assertIn(SCOPE_NATIONAL, scopes)

    def test_duplicate_uid_rendered_once(self) -> None:
        hits = dedupe_evidence_hits(
            [
                {"source": "dhis2:org_unit_child", "name": "Bulacan", "uid": "aaa"},
                {"source": "dhis2:org_unit", "name": "Bulacan Province", "uid": "AAA"},
                {"source": "dhis2:org_unit_child", "name": "Pampanga", "uid": "bbb"},
                {"source": "dhis2:org_unit_child", "name": "Pampanga", "uid": "bbb"},
            ]
        )
        self.assertEqual(len(hits), 2)
        uids = {str(h.get("uid") or "").lower() for h in hits}
        self.assertEqual(uids, {"aaa", "bbb"})

        answer = answer_from_evidence(
            REGION_III,
            {
                "usable": True,
                "hits": [
                    {"source": "dhis2:org_unit_child", "name": "Bulacan", "uid": "aaa"},
                    {"source": "dhis2:org_unit_child", "name": "Bulacan", "uid": "aaa"},
                ],
            },
        )
        self.assertIsNotNone(answer)
        assert answer is not None
        self.assertEqual(answer.count("Bulacan"), 1)


class T0ExecutionFallthroughTests(unittest.TestCase):
    def test_t0_miss_general_sets_fallthrough_flag(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor
        from hub.agent_center.routing.models import RouteRecommendation

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
        fake.dhis2_reports.search_org_units.return_value = {
            "org_units": [],
            "source": "sqlite",
        }
        fake.notepad_factory = None

        executor = RouteExecutor(fake)
        with executor._lock:
            executor._active["exec-test"] = {
                "id": "exec-test",
                "status": "queued",
                "answer": "",
                "tool_results": [],
            }
        c = classify_prompt(NATIONAL_OVERRIDE, repository_ids=["live-processing-local"])
        rec = RouteRecommendation(
            task_type=c.task_type,
            complexity=c.complexity,
            risk=c.risk,
            recommended_agent="deterministic",
            recommended_label="Deterministic",
            recommended_tier="T0",
            alternative_agent="low-cost",
            alternative_label="Low-cost",
            confidence=0.8,
            reason="test",
            estimated_usage="Very Low",
            approval_required=False,
            classification=c,
        )
        # Direct T0 path with empty evidence.
        out = executor._execute_t0(
            "exec-test",
            NATIONAL_OVERRIDE,
            rec,
            {
                "tool_ids": ["org_unit_lookup"],
                "repository_ids": ["live-processing-local"],
                "evidence_packet": {
                    "usable": False,
                    "hits": [],
                    "sources": [],
                    "tool_results": [],
                    "errors": [],
                    "summary": "No project evidence found",
                },
            },
        )
        self.assertTrue(out.get("t0_fallthrough"))
        self.assertFalse(requires_project_grounding(NATIONAL_OVERRIDE, repository_ids=["live-processing-local"]))


if __name__ == "__main__":
    unittest.main()
