from __future__ import annotations

import unittest
from unittest import mock

from hub.climate.context_registry import (
    ClimateContextRegistry,
    ClimateContextResolver,
    ContextCandidate,
    ContextEvidence,
    ContextRequest,
    ContextResolution,
    build_default_context_resolver,
)
from hub.climate.coding import ClimateCodingAdapter
from hub.climate.service import ClimateService
from hub.registry.models import Registry, Repository


class FakeSource:
    def __init__(
        self,
        source_id: str,
        *,
        available: bool = True,
        candidates: list[ContextCandidate] | None = None,
        fail: str = "",
    ) -> None:
        self.id = source_id
        self.type = "test"
        self.available = available
        self.candidates = candidates or []
        self.fail = fail
        self.search_calls = 0

    def source_metadata(self):
        return {"bounded": True}

    def availability(self, request):
        if self.fail == "availability":
            raise RuntimeError("availability failed")
        return {"available": self.available, "detail": "test source"}

    def search(self, request, *, limit):
        self.search_calls += 1
        if self.fail == "search":
            raise RuntimeError("search failed")
        return self.candidates[:limit]

    def retrieve(self, request, candidates, *, char_budget):
        if self.fail == "retrieve":
            raise RuntimeError("retrieve failed")
        return [
            ContextEvidence(
                source_id=self.id,
                reference=item.evidence_id,
                title=item.title,
                content=item.snippet[:char_budget],
                score=item.score,
                metadata={"candidate": item.evidence_id},
            )
            for item in candidates
        ]


class ContextRegistryUnitTests(unittest.TestCase):
    def test_registration_and_default_internal_sources(self) -> None:
        registry = ClimateContextRegistry()
        registry.register(FakeSource("one"))
        self.assertEqual([source.id for source in registry.sources()], ["one"])
        with self.assertRaises(ValueError):
            registry.register(FakeSource("one"))

        defaults = build_default_context_resolver(
            registry=Registry([]),
            repository_workspace=mock.Mock(),
            notebook_store=None,
            sql_workspace_store=None,
            intelligence_loader=lambda: None,
        )
        self.assertEqual(
            [source.id for source in defaults.registry.sources()],
            ["repositories", "tasks", "notebook_notes", "sql_workspace",
             "repository_activity", "gmail", "google_drive", "google_calendar",
             "dhis2_environment", "dhis2_uid_index", "dhis2_enrichment",
             "dhis2_explorer", "dhis2_reports", "dhis2_operations"],
        )

    def test_availability_skips_unavailable_sources(self) -> None:
        unavailable = FakeSource("offline", available=False)
        registry = ClimateContextRegistry()
        registry.register(unavailable)
        result = ClimateContextResolver(registry).resolve(ContextRequest("hello", "work"))
        self.assertEqual(result.sources_queried, [])
        self.assertEqual(unavailable.search_calls, 0)
        self.assertFalse(result.sources_considered[0]["available"])

    def test_bounded_retrieval_never_emits_whole_store(self) -> None:
        source = FakeSource("notes", candidates=[
            ContextCandidate("notes", f"note:{index}", f"Note {index}", "x" * 5_000)
            for index in range(20)
        ])
        registry = ClimateContextRegistry()
        registry.register(source)
        resolver = ClimateContextResolver(
            registry, max_candidates_per_source=20, max_evidence=3,
            max_chars=2_000, max_item_chars=700,
        )
        result = resolver.resolve(ContextRequest("note", "work"))
        self.assertLessEqual(len(result.evidence_references), 3)
        self.assertLessEqual(len(result.packet), 2_500)
        self.assertNotIn("note:19", result.packet)

    def test_ranking_prefers_relevant_candidate(self) -> None:
        source = FakeSource("notes", candidates=[
            ContextCandidate("notes", "note:old", "Unrelated", "miscellaneous text"),
            ContextCandidate("notes", "note:match", "Quarterly PMNP plan", "nutrition targets"),
        ])
        registry = ClimateContextRegistry()
        registry.register(source)
        result = ClimateContextResolver(registry, max_evidence=1).resolve(
            ContextRequest("PMNP plan", "work")
        )
        self.assertEqual(result.evidence_references[0]["reference"], "note:match")

    def test_source_failures_are_isolated_and_non_blocking(self) -> None:
        broken = FakeSource("broken", candidates=[], fail="search")
        healthy = FakeSource("healthy", candidates=[
            ContextCandidate("healthy", "healthy:1", "Useful", "usable evidence")
        ])
        registry = ClimateContextRegistry()
        registry.register(broken)
        registry.register(healthy)
        result = ClimateContextResolver(registry).resolve(ContextRequest("usable", "work"))
        self.assertEqual(result.sources_used, ["healthy"])
        self.assertEqual(result.failures[0]["source_id"], "broken")
        self.assertIn("usable evidence", result.packet)


class ContextRegistryClimateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.center = mock.Mock()
        self.center.repository_intelligence = None
        self.center.start_run.return_value = {
            "id": "run-context",
            "status": "queued",
            "agent_id": "gemini",
            "model": "gemini-exact",
            "conversation_id": "conversation-context",
            "repository_ids": [],
        }
        adapter = ClimateCodingAdapter(self.center)
        adapter.availability = mock.Mock(return_value={"id": "gemini", "state": "connected"})
        self.resolver = mock.Mock()
        self.resolver.resolve.return_value = ContextResolution(
            packet="CLIMATE internal context packet.\nTask evidence",
            sources_considered=[{"id": "tasks", "type": "task", "available": True}],
            sources_queried=["tasks"],
            sources_used=["tasks"],
            evidence_references=[{
                "source_id": "tasks", "reference": "note:task-1", "title": "Task evidence",
                "score": 10.0, "metadata": {"note_id": "task-1"},
            }],
            failures=[],
        )
        workspace = mock.Mock()
        workspace.preview.return_value = {"content": "selected", "binary": False}
        self.service = ClimateService(
            Registry([Repository(id="repo-a", name="Repo A", type="command", enabled=True, tags=["work"])]),
            workspace,
            adapter,
            context_resolver=self.resolver,
        )

    def _run(self, **overrides):
        payload = {
            "provider": "gemini",
            "model": "gemini-exact",
            "prompt": "show my task",
            "execution_mode": "climate_assisted",
            "context_scope": "general",
        }
        payload.update(overrides)
        return self.service.execute_chat("work", **payload)

    def test_airix_general_retrieves_internal_context(self) -> None:
        result = self._run()
        request = self.resolver.resolve.call_args.args[0]
        self.assertEqual(request.scope, "general")
        self.assertIn("Task evidence", self.center.start_run.call_args.args[0]["prompt"])
        self.assertEqual(result["sources_used"], ["tasks"])

    def test_repository_specific_scope_is_forwarded(self) -> None:
        self._run(context_scope="repository", repository_id="repo-a")
        request = self.resolver.resolve.call_args.args[0]
        self.assertEqual(request.scope, "repository")
        self.assertEqual(request.repository_id, "repo-a")

    def test_direct_mode_is_fully_isolated_from_context_resolver(self) -> None:
        result = self._run(execution_mode="direct", context_scope="all")
        self.resolver.resolve.assert_not_called()
        provider_payload = self.center.start_run.call_args.args[0]
        self.assertEqual(provider_payload["prompt"], "show my task")
        self.assertEqual(result["sources_considered"], [])
        self.assertEqual(result["sources_used"], [])

    def test_source_metadata_is_in_persisted_execution_record(self) -> None:
        result = self._run()
        persisted = self.center.start_run.call_args.args[0]["climate_execution"]
        self.assertEqual(persisted["sources_queried"], ["tasks"])
        self.assertEqual(persisted["sources_used"], ["tasks"])
        self.assertEqual(persisted["evidence_references"][0]["reference"], "note:task-1")
        self.assertEqual(result["sources_considered"][0]["id"], "tasks")


if __name__ == "__main__":
    unittest.main()
