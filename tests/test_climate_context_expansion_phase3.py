from __future__ import annotations

import unittest
from unittest import mock

from hub.climate.coding import ClimateCodingAdapter
from hub.climate.context_registry import ContextRequest, build_default_context_resolver
from hub.climate.dhis2_sources import (
    Dhis2EnrichmentContextSource,
    Dhis2EnvironmentContextSource,
    Dhis2ExplorerContextSource,
    Dhis2UidIndexContextSource,
    dhis2_write_methods,
)
from hub.climate.service import ClimateService
from hub.dhis2.client import Dhis2Client
from hub.registry.models import Registry, Repository


class FakeDhis2Client:
    def __init__(
        self,
        *,
        enabled: bool = True,
        configured: bool = True,
        results: list[dict] | None = None,
        metadata: dict | None = None,
        fail: str = "",
        password: str = "super-secret-dhis2",
    ) -> None:
        self.enabled = enabled
        self.configured = configured
        self.results = results or []
        self.metadata = metadata or {}
        self.fail = fail
        self.password = password
        self._secrets = [password]
        self.settings = mock.Mock(password=password, username="dhis2-user")
        self.calls: list[tuple] = []

    def public_config(self):
        self.calls.append(("public_config",))
        return {
            "enabled": self.enabled,
            "configured": self.configured,
            "mode": "readonly",
            "allow_writes": False,
            "environment": "stage",
            "base_url": "https://dhis2.example.invalid",
            "username_set": True,
            "password_set": True,
        }

    def writes_allowed(self):
        return False

    def search(self, query, *, limit=25):
        self.calls.append(("search", query, limit))
        if self.fail == "search":
            raise RuntimeError("dhis2 unreachable")
        return {"ok": True, "results": list(self.results)[:limit], "query": query}

    def get_metadata(self, resource_type, uid):
        self.calls.append(("get_metadata", resource_type, uid))
        if self.fail == "retrieve":
            raise RuntimeError("metadata failed")
        return {
            "ok": True,
            "resource_type": resource_type,
            "raw_fields": dict(self.metadata) or {
                "id": uid, "name": "Weight", "shortName": "WT", "description": "Child weight",
            },
        }

    def get_analytics(self, *args, **kwargs):
        self.calls.append(("get_analytics", args, kwargs))
        raise AssertionError("must not dump analytics/linelists")

    def get_text(self, *args, **kwargs):
        raise AssertionError("must not fetch report HTML")

    def create(self, *args, **kwargs):
        raise AssertionError("DHIS2 writes are forbidden")

    def execute_sql(self, *args, **kwargs):
        raise AssertionError("must not run SQL from prompts")


class FakeUidStore:
    def __init__(self, records: list[dict] | None = None) -> None:
        self._records = records or []

    def records(self):
        return list(self._records)


class FakeUidIndex:
    def __init__(self, records: list[dict] | None = None) -> None:
        self.mapping_store = FakeUidStore(records)


class FakeEnrichment:
    def __init__(self, rows: list[dict] | None = None, rels: list[dict] | None = None, snap: str = "snap-1") -> None:
        self.rows = rows or []
        self.rels = rels or []
        self.snap = snap
        self.calls: list[tuple] = []

    def current_snapshot_id(self):
        return self.snap

    def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        q = str(kwargs.get("q") or "").lower()
        rows = [
            row for row in self.rows
            if not q or q in str(row.get("name") or "").lower() or q in str(row.get("uid") or "").lower()
        ]
        return rows[: kwargs.get("limit") or 8], len(rows)

    def relationships_for(self, uid, snapshot_id=None):
        self.calls.append(("relationships_for", uid))
        return [row for row in self.rels if row.get("from_uid") == uid or row.get("to_uid") == uid][:8]

    def apply(self, *args, **kwargs):
        raise AssertionError("enrichment apply is a write")


class FakeReports:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_standard_library(self, q=""):
        self.calls.append(("list_standard_library", q))
        return {
            "sections": [{
                "environment": "stage",
                "reports": [{
                    "uid": "Rabcdefghij",
                    "name": "PMNP coverage report",
                    "report_type": "HTML",
                    "html_available": True,
                    "html": "<html>SECRET entire report</html>",
                }],
            }]
        }

    def search_org_units(self, environment, **kwargs):
        self.calls.append(("search_org_units", environment, kwargs))
        if kwargs.get("refresh"):
            raise AssertionError("must not force DHIS2 OU refresh")
        return {"org_units": [{"id": "Oabcdefghij", "displayName": "Region III", "level": 2}]}

    def generate(self, *args, **kwargs):
        raise AssertionError("report generate is not a context read")


class FakeJobs:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_recent(self, *, limit=50, status=None):
        self.calls.append(("list_recent", limit, status))
        return [
            {
                "id": "job_dhis",
                "capability_id": "dhis2_metadata_lookup",
                "repository_id": "hub",
                "status": "completed",
                "created_at": "2026-08-19",
            },
            {
                "id": "job_other",
                "capability_id": "repo_health",
                "repository_id": "demo",
                "status": "completed",
                "created_at": "2026-08-19",
            },
        ]


class FakeAudit:
    def list_recent(self, limit=100):
        return [
            {
                "action": "DHIS2_METADATA_LOOKUP",
                "target": "Deabcdefghij",
                "detail": "password=super-secret-dhis2",
                "ok": True,
                "timestamp": "2026-08-19T00:00:00+00:00",
            },
            {"action": "EMAIL_VIEW", "target": "inbox", "detail": "ok", "ok": True, "timestamp": "t2"},
        ]


def _resolver(**kwargs):
    return build_default_context_resolver(
        registry=Registry([
            Repository(id="repo-a", name="Repo A", type="command", enabled=True, tags=["work"]),
        ]),
        repository_workspace=mock.Mock(),
        notebook_store=None,
        sql_workspace_store=None,
        intelligence_loader=lambda: None,
        **kwargs,
    )


class Dhis2ContextSourceTests(unittest.TestCase):
    def test_disabled_and_unconfigured_are_skipped(self) -> None:
        disabled = FakeDhis2Client(enabled=False)
        result = _resolver(dhis2_client=disabled).resolve(
            ContextRequest("DHIS2 instance config", "work")
        )
        considered = {row["id"]: row for row in result.sources_considered}
        self.assertFalse(considered["dhis2_environment"]["available"])
        self.assertFalse(considered["dhis2_explorer"]["available"])
        self.assertNotIn("dhis2_environment", result.sources_queried)

        empty_uid = FakeUidIndex([])
        result2 = _resolver(uid_index=empty_uid).resolve(
            ContextRequest("Weight UID", "work")
        )
        considered2 = {row["id"]: row for row in result2.sources_considered}
        self.assertFalse(considered2["dhis2_uid_index"]["available"])

    def test_connected_uid_and_environment_retrieve(self) -> None:
        client = FakeDhis2Client()
        uid = FakeUidIndex([{
            "uid": "Deabcdefghij",
            "name": "Weight",
            "code": "WEIGHT",
            "object_type": "dataElement",
            "source_repository": "fixture",
        }])
        result = _resolver(dhis2_client=client, uid_index=uid).resolve(
            ContextRequest("Weight data element UID", "work")
        )
        self.assertIn("dhis2_uid_index", result.sources_queried)
        self.assertIn("dhis2_uid_index", result.sources_used)
        refs = {row["source_id"]: row for row in result.evidence_references}
        self.assertEqual(refs["dhis2_uid_index"]["metadata"]["uid"], "Deabcdefghij")
        self.assertIn("Deabcdefghij", result.packet)

    def test_search_and_retrieve_are_bounded(self) -> None:
        records = [
            {"uid": f"De{index:09d}x", "name": f"Weight {index}", "object_type": "dataElement"}
            for index in range(40)
        ]
        source = Dhis2UidIndexContextSource(FakeUidIndex(records))
        request = ContextRequest("Weight", "work")
        found = source.search(request, limit=8)
        self.assertLessEqual(len(found), 8)
        evidence = source.retrieve(request, found, char_budget=400)
        self.assertLessEqual(sum(len(item.content) for item in evidence), 400)

        client = FakeDhis2Client(results=[
            {"id": f"I{index:010d}", "name": f"Coverage {index}", "resource_type": "indicators"}
            for index in range(40)
        ])
        explorer = Dhis2ExplorerContextSource(client)
        found2 = explorer.search(ContextRequest("DHIS2 coverage indicator", "work"), limit=8)
        self.assertLessEqual(len(found2), 8)
        sizes = [call[2] for call in client.calls if call[0] == "search"]
        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), 8)

    def test_write_isolation_and_no_sql_or_linelists(self) -> None:
        self.assertEqual(dhis2_write_methods(Dhis2Client), [])
        self.assertFalse(FakeDhis2Client().writes_allowed())
        client = FakeDhis2Client(results=[{
            "id": "Deabcdefghij",
            "name": "Weight",
            "resource_type": "dataElements",
            "resource_label": "Data Element",
        }])
        source = Dhis2ExplorerContextSource(client)
        request = ContextRequest("DHIS2 Weight data element", "work")
        found = source.search(request, limit=4)
        source.retrieve(request, found, char_budget=400)
        self.assertFalse(any(call[0] == "get_analytics" for call in client.calls))
        self.assertFalse(any(call[0] == "create" for call in client.calls))
        env = Dhis2EnvironmentContextSource(client)
        self.assertEqual(env.search(request, limit=2)[0].metadata["allow_writes"], False)

    def test_failure_isolation_does_not_block_other_sources(self) -> None:
        client = FakeDhis2Client(fail="search")
        uid = FakeUidIndex([{
            "uid": "Deabcdefghij",
            "name": "Weight",
            "object_type": "dataElement",
        }])
        result = _resolver(dhis2_client=client, uid_index=uid).resolve(
            ContextRequest("Weight data element DHIS2", "work")
        )
        self.assertTrue(any(row["source_id"] == "dhis2_explorer" for row in result.failures))
        self.assertIn("dhis2_uid_index", result.sources_used)

    def test_repository_and_all_and_personal_isolation(self) -> None:
        uid = FakeUidIndex([{"uid": "Deabcdefghij", "name": "Weight", "object_type": "dataElement"}])
        client = FakeDhis2Client(results=[{"id": "Deabcdefghij", "name": "Weight", "resource_type": "dataElements"}])
        resolver = _resolver(dhis2_client=client, uid_index=uid)
        repo = resolver.resolve(ContextRequest("Weight DHIS2", "work", scope="repository", repository_id="repo-a"))
        all_scope = resolver.resolve(ContextRequest("Weight DHIS2", "work", scope="all"))
        personal = resolver.resolve(ContextRequest("Weight DHIS2", "personal"))
        for result in (repo, all_scope, personal):
            considered = {row["id"]: row for row in result.sources_considered}
            for source_id in (
                "dhis2_environment", "dhis2_uid_index", "dhis2_enrichment",
                "dhis2_explorer", "dhis2_reports", "dhis2_operations",
            ):
                self.assertFalse(considered[source_id]["available"])
                self.assertNotIn(source_id, result.sources_queried)
        self.assertFalse(any(call[0] == "search" for call in client.calls))

    def test_enrichment_relationships_are_bounded(self) -> None:
        store = FakeEnrichment(
            rows=[{
                "uid": "Deabcdefghij",
                "name": "Weight",
                "object_type": "dataElement",
                "audit_status_list": ["missing_option_set"],
            }],
            rels=[{
                "rel_type": "DATA_ELEMENT_IN_PROGRAM_STAGE",
                "from_uid": "Deabcdefghij",
                "to_uid": "PSabcdefghij",
                "to_name": "Child visit",
            }],
        )
        source = Dhis2EnrichmentContextSource(store)
        request = ContextRequest("Weight enrichment audit", "work")
        found = source.search(request, limit=4)
        evidence = source.retrieve(request, found, char_budget=800)
        self.assertIn("missing_option_set", evidence[0].content)
        self.assertIn("DATA_ELEMENT_IN_PROGRAM_STAGE", evidence[0].content)
        self.assertNotIn("apply", store.calls[0])

    def test_credential_redaction(self) -> None:
        client = FakeDhis2Client(results=[{
            "id": "Deabcdefghij",
            "name": "Weight super-secret-dhis2",
            "resource_type": "dataElements",
            "resource_label": "Data Element",
        }])
        result = _resolver(dhis2_client=client, audit_store=FakeAudit()).resolve(
            ContextRequest("DHIS2 Weight data element", "work")
        )
        self.assertNotIn("super-secret-dhis2", result.packet)
        self.assertNotIn("dhis2-user", result.packet)
        env = Dhis2EnvironmentContextSource(client)
        found = env.search(ContextRequest("DHIS2 instance config", "work"), limit=2)
        evidence = env.retrieve(ContextRequest("DHIS2 instance config", "work"), found, char_budget=800)
        self.assertNotIn("super-secret-dhis2", evidence[0].content)
        self.assertNotIn("dhis2-user", evidence[0].content)
        self.assertIn("secret configured", evidence[0].content.lower())


class Dhis2ClimateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.center = mock.Mock()
        self.center.repository_intelligence = None
        self.center.start_run.return_value = {
            "id": "run-dhis2",
            "status": "queued",
            "agent_id": "gemini",
            "model": "gemini-exact",
            "conversation_id": "conversation-dhis2",
            "repository_ids": [],
        }
        adapter = ClimateCodingAdapter(self.center)
        adapter.availability = mock.Mock(return_value={"id": "gemini", "state": "connected"})
        self.uid = FakeUidIndex([{
            "uid": "Deabcdefghij",
            "name": "Weight",
            "code": "WEIGHT",
            "object_type": "dataElement",
        }])
        workspace = mock.Mock()
        workspace.preview.return_value = {"content": "selected", "binary": False}
        self.service = ClimateService(
            Registry([]),
            workspace,
            adapter,
            uid_index=self.uid,
            dhis2_client=FakeDhis2Client(),
            dhis2_reports=FakeReports(),
            job_store=FakeJobs(),
            audit_store=FakeAudit(),
        )

    def _run(self, **overrides):
        payload = {
            "provider": "gemini",
            "model": "gemini-exact",
            "prompt": "Weight data element DHIS2 UID",
            "execution_mode": "climate_assisted",
            "context_scope": "general",
        }
        payload.update(overrides)
        return self.service.execute_chat("work", **payload)

    def test_general_airix_retrieves_dhis2(self) -> None:
        result = self._run()
        self.assertIn("dhis2_uid_index", result["sources_used"])
        prompt = self.center.start_run.call_args.args[0]["prompt"]
        self.assertIn("Deabcdefghij", prompt)
        self.assertNotIn("<html>", prompt)

    def test_direct_mode_never_auto_queries_dhis2(self) -> None:
        client = FakeDhis2Client()
        self.service = ClimateService(
            Registry([]),
            mock.Mock(preview=mock.Mock(return_value={"content": "x", "binary": False})),
            self.service.coding,
            uid_index=self.uid,
            dhis2_client=client,
        )
        result = self.service.execute_chat(
            "work",
            provider="gemini",
            model="gemini-exact",
            prompt="Weight data element DHIS2 UID",
            execution_mode="direct",
            context_scope="general",
        )
        self.assertEqual(client.calls, [])
        self.assertEqual(result["sources_considered"], [])
        self.assertEqual(result["sources_used"], [])

    def test_persistence_records_provenance(self) -> None:
        result = self._run()
        persisted = self.center.start_run.call_args.args[0]["climate_execution"]
        self.assertIn("dhis2_uid_index", persisted["sources_queried"])
        self.assertIn("dhis2_uid_index", persisted["sources_used"])
        refs = {row["source_id"]: row for row in persisted["evidence_references"]}
        self.assertEqual(refs["dhis2_uid_index"]["reference"], "dhis2-uid:Deabcdefghij")
        self.assertIn("dhis2_uid_index", [row["id"] for row in result["sources_considered"]])

    def test_reports_do_not_embed_html(self) -> None:
        reports = FakeReports()
        result = _resolver(dhis2_reports=reports).resolve(
            ContextRequest("DHIS2 coverage report", "work")
        )
        self.assertNotIn("<html>", result.packet)
        self.assertNotIn("SECRET entire report", result.packet)
        self.assertFalse(any(call[0] == "generate" for call in reports.calls))


class Phase12RegressionTests(unittest.TestCase):
    def test_prior_sources_and_caps_remain(self) -> None:
        defaults = _resolver()
        ids = [source.id for source in defaults.registry.sources()]
        self.assertEqual(ids[:8], [
            "repositories", "tasks", "notebook_notes", "sql_workspace",
            "repository_activity", "gmail", "google_drive", "google_calendar",
        ])
        self.assertEqual(defaults.max_candidates_per_source, 12)
        self.assertEqual(defaults.max_evidence, 8)
        self.assertEqual(defaults.max_chars, 12_000)
        self.assertEqual(defaults.max_item_chars, 2_400)


if __name__ == "__main__":
    unittest.main()
