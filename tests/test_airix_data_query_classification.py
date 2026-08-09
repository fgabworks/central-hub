"""AiriX dynamic data-query classification + routing (no hard-coded places/types)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from hub.agent_center.data_intent import detect_data_query_intent, extract_data_filters
from hub.agent_center.routing.classifier import classify_prompt
from hub.agent_center.routing.context import provider_to_adapter_id, select_minimal_tools
from hub.agent_center.routing.models import RoutingSettings
from hub.agent_center.routing.providers import ProviderRegistry
from hub.agent_center.routing.router import recommend_route
from hub.agent_center.scope import SCOPE_DHIS2, detect_prompt_scope


REPO = ["live-processing-local"]

# Diverse prompts — locations/groups/periods are examples of shape, not a catalog.
PROMPTS = {
    "barangay_count": "Count the number of pregnant women in Brgy. Baloy for 2026 Q2.",
    "barangay_abbrev": "How many eligible children in Bgy. San Jose for 2025Q3?",
    "municipality_households": "Total households in Mun. Capas for FY 2026",
    "city_pct": "What is the coverage percentage for City Angeles in 2026 Q1?",
    "province_beneficiaries": "Count beneficiaries by province for 2025 Q4",
    "region_breakdown": "Show beneficiary totals by region for last quarter",
        "numerator_denominator": "Numerator and denominator for indicator A1b2C3d4E5f in Region III",
        "uid_status": "Show approved records for ou A1b2C3d4E5f for 2026Q2",
    "eligible_ou": "How many eligible members under organisation unit North District?",
    "national_count": "Count national eligible population for 2026 Q2",
}


class DataIntentDetectionTests(unittest.TestCase):
    def test_brgy_abbreviation_is_data_query(self) -> None:
        intent = detect_data_query_intent(PROMPTS["barangay_count"])
        self.assertTrue(intent.is_data_query)
        self.assertIn("barangay", intent.entity_types)
        self.assertEqual(intent.filters.get("location"), "Baloy")
        self.assertTrue(any("2026" in str(p) for p in intent.filters.get("period") or []))
        self.assertIn("pregnant women", (intent.filters.get("population_group") or "").lower())

    def test_diverse_locations_and_admin_levels(self) -> None:
        for key in (
            "barangay_abbrev",
            "municipality_households",
            "city_pct",
            "province_beneficiaries",
            "region_breakdown",
            "eligible_ou",
        ):
            with self.subTest(key=key):
                intent = detect_data_query_intent(PROMPTS[key])
                self.assertTrue(intent.is_data_query, msg=intent.reason)
                self.assertTrue(intent.entity_types or intent.filters)

    def test_indicator_and_uid_filters(self) -> None:
        intent = detect_data_query_intent(PROMPTS["numerator_denominator"])
        self.assertTrue(intent.is_data_query)
        self.assertTrue(intent.filters.get("indicator_ref"))
        self.assertTrue(intent.filters.get("uids") or "region" in intent.entity_types)

        status_intent = detect_data_query_intent(PROMPTS["uid_status"])
        self.assertTrue(status_intent.is_data_query)
        self.assertIn("approved", status_intent.filters.get("status") or [])
        self.assertTrue(status_intent.filters.get("uids"))

    def test_extract_filters_period_and_environment(self) -> None:
        filters = extract_data_filters(
            "Count records in Brgy. Sample for 2026 Q2 on staging"
        )
        self.assertEqual(filters.get("location"), "Sample")
        self.assertTrue(filters.get("period"))
        self.assertEqual(filters.get("environment"), "staging")

    def test_gk_prompt_is_not_data_query(self) -> None:
        intent = detect_data_query_intent("Explain what a Python list comprehension is")
        self.assertFalse(intent.is_data_query)


class DataQueryScopeAndClassifyTests(unittest.TestCase):
    def test_baloy_prompt_is_dhis2_not_gk(self) -> None:
        prompt = PROMPTS["barangay_count"]
        scope = detect_prompt_scope(prompt, repository_ids=REPO)
        self.assertEqual(scope.kind, SCOPE_DHIS2)
        self.assertTrue(scope.try_deterministic_tools)
        self.assertFalse(scope.allow_general_knowledge)
        # With repo + locality, project evidence is required.
        self.assertTrue(scope.requires_project_evidence)

        c = classify_prompt(prompt, repository_ids=REPO)
        self.assertEqual(c.task_type, "lookup")
        self.assertTrue(c.deterministic_capable)
        self.assertIn("data_query", c.signals)
        self.assertIn("authoritative_data_query", c.signals)
        self.assertNotIn("simple_general_knowledge", c.signals)

    def test_generalizes_across_prompt_shapes(self) -> None:
        for key, prompt in PROMPTS.items():
            with self.subTest(key=key):
                c = classify_prompt(prompt, repository_ids=REPO)
                self.assertIn("data_query", c.signals)
                self.assertTrue(c.deterministic_capable)
                self.assertNotIn("simple_general_knowledge", c.signals)
                self.assertEqual(c.task_type, "lookup")


class DataQueryRoutingTests(unittest.TestCase):
    def _recommend(self, prompt: str, **settings_kw):
        c = classify_prompt(prompt, repository_ids=REPO)
        settings = RoutingSettings(prefer_deterministic=True, **settings_kw)
        return recommend_route(
            c,
            settings=settings,
            registry=ProviderRegistry(),
            available_provider_ids={
                "deterministic",
                "low-cost",
                "hub-simulator",
                "openai-api",
                "grok",
                "codex",
            },
        )

    def test_routes_t0_not_hub_simulator(self) -> None:
        rec = self._recommend(PROMPTS["barangay_count"])
        self.assertEqual(rec.recommended_tier, "T0")
        self.assertEqual(rec.recommended_agent, "deterministic")
        self.assertNotIn(rec.recommended_agent, {"low-cost", "hub-simulator"})
        self.assertNotEqual(provider_to_adapter_id(rec.recommended_agent), "hub-simulator")
        if rec.alternative_agent:
            self.assertNotIn(rec.alternative_agent, {"low-cost", "hub-simulator"})

    def test_cheapest_mode_still_avoids_simulator_for_data(self) -> None:
        rec = self._recommend(PROMPTS["municipality_households"], mode="cheapest")
        self.assertEqual(rec.recommended_agent, "deterministic")
        self.assertNotIn(rec.recommended_agent, {"low-cost", "hub-simulator"})

    def test_data_lookup_tools_prefer_ou_and_reports(self) -> None:
        c = classify_prompt(PROMPTS["barangay_count"], repository_ids=REPO)
        tools = select_minimal_tools(c)
        self.assertIn("org_unit_lookup", tools)
        self.assertTrue(any(t.endswith("_lookup") for t in tools))


class DataQueryExecutionMissTests(unittest.TestCase):
    def test_t0_miss_returns_cannot_verify_not_simulator(self) -> None:
        from hub.agent_center.routing.execution import RouteExecutor

        prompt = PROMPTS["barangay_count"]
        c = classify_prompt(prompt, repository_ids=REPO)
        rec = recommend_route(
            c,
            settings=RoutingSettings(prefer_deterministic=True),
            registry=ProviderRegistry(),
            available_provider_ids={"deterministic", "low-cost", "hub-simulator", "grok"},
        )
        self.assertEqual(rec.recommended_agent, "deterministic")

        svc = RouteExecutor.__new__(RouteExecutor)
        svc._lock = __import__("threading").RLock()
        svc._active = {}
        svc._fingerprints = {}
        svc._availability_loader = lambda: {
            "hub-simulator": {"status": "available", "runnable": True},
            "grok": {"status": "available", "runnable": True},
        }
        svc.agent_center = MagicMock()
        svc._tools_context = MagicMock(return_value=MagicMock())
        svc._update = lambda eid, **kw: {**(svc._active.get(eid) or {"id": eid}), **kw}
        svc.get_status = lambda eid: svc._active.get(eid)
        svc._fail = lambda eid, msg, code="": {
            "id": eid,
            "status": "failed",
            "error": msg,
            "error_code": code,
        }
        svc._record_history = MagicMock()
        svc._provider_available = lambda aid: (True, "ok")
        svc._execute_agent = MagicMock(
            side_effect=AssertionError("must not escalate authoritative data to an agent")
        )

        empty_packet = {
            "usable": False,
            "hits": [],
            "sources": [],
            "tool_results": [],
            "errors": ["no analytics"],
            "summary": "No usable hits",
        }

        with patch(
            "hub.agent_center.grounding.collect_evidence_packet",
            return_value=empty_packet,
        ), patch(
            "hub.agent_center.grounding.answer_from_evidence",
            return_value="",
        ):
            context = {
                "tool_ids": ["org_unit_lookup", "uid_lookup"],
                "repository_ids": REPO,
                "evidence_packet": empty_packet,
            }
            svc._active["exec1"] = {"id": "exec1", "status": "queued"}
            result = svc._execute_t0("exec1", prompt, rec, context)

        self.assertFalse(result.get("t0_fallthrough"))
        answer = str(result.get("answer") or "")
        self.assertIn("Cannot verify", answer)
        self.assertNotIn("Hub Simulator", answer)
        svc._execute_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
