from __future__ import annotations

import unittest
from unittest import mock

from hub.climate.coding import ClimateCodingAdapter
from hub.climate.context_registry import (
    ClimateContextResolver,
    ContextRequest,
    build_default_context_resolver,
)
from hub.climate.external_sources import GmailContextSource
from hub.climate.service import ClimateService
from hub.drive.api import DriveClient
from hub.drive.models import FORBIDDEN_DRIVE_ACTIONS, MAX_EXPORT_CHARS
from hub.drive.service import DriveService, DriveServiceError, _drive_search_query
from hub.email.models import DRIVE_SCOPES, FORBIDDEN_GMAIL_ACTIONS, google_api_scopes_for_account
from hub.registry.models import Registry, Repository


class FakeEmail:
    def __init__(
        self,
        *,
        accounts: list[dict] | None = None,
        messages: list[dict] | None = None,
        bodies: dict[str, str] | None = None,
        fail: str = "",
    ) -> None:
        self.accounts = accounts or []
        self.messages = messages or []
        self.bodies = bodies or {}
        self.fail = fail
        self.calls: list[tuple] = []

    def list_accounts(self, workspace):
        self.calls.append(("list_accounts", workspace))
        return list(self.accounts)

    def search_messages(self, account_id, *, q, page_size=8):
        self.calls.append(("search_messages", account_id, q, page_size))
        if self.fail == "search":
            raise RuntimeError("gmail search failed")
        return {"ok": True, "messages": list(self.messages)[:page_size]}

    def get_message(self, account_id, message_id, **kwargs):
        self.calls.append(("get_message", account_id, message_id))
        if self.fail == "retrieve":
            raise RuntimeError("gmail retrieve failed")
        return {
            "ok": True,
            "message": {
                "id": message_id,
                "body_text": self.bodies.get(message_id, "bounded body"),
                "snippet": "snippet",
            },
        }

    def get_thread(self, *args, **kwargs):
        self.calls.append(("get_thread", args, kwargs))
        raise AssertionError("must not retrieve entire threads")

    def send(self, *args, **kwargs):
        raise AssertionError("Gmail writes are forbidden")


class FakeDrive:
    def __init__(
        self,
        *,
        accounts: list[dict] | None = None,
        files: list[dict] | None = None,
        exports: dict[str, str] | None = None,
        fail: str = "",
    ) -> None:
        self._accounts = accounts or []
        self.files = files or []
        self.exports = exports or {}
        self.fail = fail
        self.calls: list[tuple] = []

    def connected_accounts(self, workspace):
        self.calls.append(("connected_accounts", workspace))
        return list(self._accounts)

    def search_files(self, workspace, *, q, limit=8):
        self.calls.append(("search_files", workspace, q, limit))
        if self.fail == "search":
            raise RuntimeError("drive search failed")
        return list(self.files)[:limit]

    def get_file(self, account_id, file_id, *, include_export=True, char_budget=2400):
        self.calls.append(("get_file", account_id, file_id, include_export, char_budget))
        if self.fail == "retrieve":
            raise RuntimeError("drive retrieve failed")
        export = (self.exports.get(file_id) or "exported snippet")[:char_budget]
        return {
            "ok": True,
            "file": {
                "id": file_id,
                "name": "Doc",
                "mime_type": "application/vnd.google-apps.document",
                "description": "",
                "export_text": export if include_export else "",
            },
        }

    def create(self, *args, **kwargs):
        raise AssertionError("Drive writes are forbidden")


class FakeCalendar:
    def __init__(
        self,
        *,
        accounts: list[dict] | None = None,
        events: list[dict] | None = None,
        fail: str = "",
    ) -> None:
        self.accounts = accounts or []
        self.events = events or []
        self.fail = fail
        self.calls: list[tuple] = []

    def list_accounts(self, workspace):
        self.calls.append(("list_accounts", workspace))
        return list(self.accounts)

    def search_events(self, workspace, *, q, limit=8):
        self.calls.append(("search_events", workspace, q, limit))
        if self.fail == "search":
            raise RuntimeError("calendar search failed")
        return list(self.events)[:limit]

    def create_event(self, *args, **kwargs):
        raise AssertionError("Calendar writes are forbidden")


def _gmail_account(**overrides):
    row = {
        "id": "acct-mail",
        "email": "me@example.com",
        "status": "connected",
        "has_gmail": True,
        "workspace": "work",
    }
    row.update(overrides)
    return row


def _drive_account(**overrides):
    row = {
        "id": "acct-drive",
        "email": "me@example.com",
        "status": "connected",
        "has_drive": True,
        "workspace": "work",
    }
    row.update(overrides)
    return row


def _calendar_account(**overrides):
    row = {
        "id": "acct-cal",
        "email": "me@example.com",
        "status": "connected",
        "has_calendar": True,
        "workspace": "work",
    }
    row.update(overrides)
    return row


def _resolver(email=None, calendar=None, drive=None, **kwargs):
    return build_default_context_resolver(
        registry=Registry([
            Repository(id="repo-a", name="Repo A", type="command", enabled=True, tags=["work"]),
        ]),
        repository_workspace=mock.Mock(),
        notebook_store=None,
        sql_workspace_store=None,
        intelligence_loader=lambda: None,
        email_service=email,
        calendar_service=calendar,
        drive_service=drive,
        **kwargs,
    )


class ExternalSourceContractTests(unittest.TestCase):
    def test_disconnected_integrations_are_skipped(self) -> None:
        email = FakeEmail(accounts=[_gmail_account(status="unavailable")])
        drive = FakeDrive(accounts=[])
        calendar = FakeCalendar(accounts=[_calendar_account(has_calendar=False)])
        result = _resolver(email, calendar, drive).resolve(
            ContextRequest("quarterly PMNP plan", "work")
        )
        considered = {row["id"]: row for row in result.sources_considered}
        for source_id in ("gmail", "google_drive", "google_calendar"):
            self.assertFalse(considered[source_id]["available"])
            self.assertNotIn(source_id, result.sources_queried)
        self.assertEqual(email.calls, [("list_accounts", "work")])
        self.assertEqual([name for name, *_ in drive.calls], ["connected_accounts"])
        self.assertEqual(calendar.calls, [("list_accounts", "work")])

    def test_connected_integrations_search_and_retrieve(self) -> None:
        email = FakeEmail(
            accounts=[_gmail_account()],
            messages=[{
                "id": "m1",
                "thread_id": "t1",
                "subject": "PMNP plan",
                "from_addr": "boss@example.com",
                "date_header": "Wed, 19 Aug 2026",
                "snippet": "nutrition targets",
            }],
            bodies={"m1": "Please review the PMNP nutrition targets."},
        )
        drive = FakeDrive(
            accounts=[_drive_account()],
            files=[{
                "id": "f1",
                "account_id": "acct-drive",
                "name": "PMNP plan doc",
                "mime_type": "application/vnd.google-apps.document",
                "modified_time": "2026-08-19",
                "owners": ["me"],
                "description": "plan",
            }],
            exports={"f1": "Doc body about PMNP plan."},
        )
        calendar = FakeCalendar(
            accounts=[_calendar_account()],
            events=[{
                "id": "e1",
                "account_id": "acct-cal",
                "calendar_id": "primary",
                "summary": "PMNP plan review",
                "start": {"date_time": "2026-08-20T09:00:00Z"},
                "end": {"date_time": "2026-08-20T10:00:00Z"},
                "location": "Manila",
                "attendees": [{"email": "me@example.com"}],
                "description": "Discuss PMNP plan",
            }],
        )
        result = _resolver(email, calendar, drive).resolve(
            ContextRequest("PMNP plan", "work")
        )
        self.assertIn("gmail", result.sources_queried)
        self.assertIn("google_drive", result.sources_queried)
        self.assertIn("google_calendar", result.sources_queried)
        self.assertTrue({"gmail", "google_drive", "google_calendar"} & set(result.sources_used))
        refs = {row["source_id"]: row for row in result.evidence_references}
        self.assertEqual(refs["gmail"]["metadata"]["message_id"], "m1")
        self.assertEqual(refs["google_drive"]["metadata"]["file_id"], "f1")
        self.assertEqual(refs["google_calendar"]["metadata"]["event_id"], "e1")
        self.assertIn("PMNP", result.packet)
        self.assertNotIn(("get_thread",), [(c[0],) if c[0] == "get_thread" else () for c in email.calls])
        self.assertFalse(any(call[0] == "get_thread" for call in email.calls))

    def test_search_and_retrieve_are_bounded(self) -> None:
        messages = [
            {
                "id": f"m{i}",
                "subject": f"PMNP note {i}",
                "from_addr": "a@example.com",
                "snippet": "x" * 8_000,
            }
            for i in range(40)
        ]
        email = FakeEmail(accounts=[_gmail_account()], messages=messages, bodies={
            f"m{i}": "Y" * 20_000 for i in range(40)
        })
        source = GmailContextSource(email)
        request = ContextRequest("PMNP note", "work")
        found = source.search(request, limit=8)
        self.assertLessEqual(len(found), 8)
        evidence = source.retrieve(request, found[:2], char_budget=600)
        self.assertLessEqual(len(evidence), 2)
        self.assertLessEqual(sum(len(item.content) for item in evidence), 600)
        self.assertTrue(all("Y" * 5_000 not in item.content for item in evidence))
        sizes = [call[3] for call in email.calls if call[0] == "search_messages"]
        self.assertTrue(sizes)
        self.assertLessEqual(max(sizes), 8)

    def test_failure_isolation_does_not_block_other_sources(self) -> None:
        email = FakeEmail(accounts=[_gmail_account()], fail="search")
        drive = FakeDrive(
            accounts=[_drive_account()],
            files=[{
                "id": "f1",
                "account_id": "acct-drive",
                "name": "Useful PMNP file",
                "mime_type": "application/vnd.google-apps.document",
                "description": "usable evidence",
            }],
            exports={"f1": "usable evidence from Drive"},
        )
        result = _resolver(email, None, drive).resolve(ContextRequest("usable evidence", "work"))
        self.assertEqual(result.failures[0]["source_id"], "gmail")
        self.assertIn("google_drive", result.sources_used)
        self.assertIn("usable evidence from Drive", result.packet)

    def test_ranking_prefers_stronger_external_candidate(self) -> None:
        email = FakeEmail(
            accounts=[_gmail_account()],
            messages=[{
                "id": "m-noise",
                "subject": "Lunch menu",
                "from_addr": "cafe@example.com",
                "snippet": "soup of the day",
            }],
        )
        drive = FakeDrive(
            accounts=[_drive_account()],
            files=[{
                "id": "f-hit",
                "account_id": "acct-drive",
                "name": "Quarterly PMNP plan",
                "mime_type": "application/vnd.google-apps.document",
                "description": "nutrition targets",
            }],
            exports={"f-hit": "Quarterly PMNP plan nutrition targets"},
        )
        result = ClimateContextResolver(
            _resolver(email, None, drive).registry, max_evidence=1
        ).resolve(ContextRequest("PMNP plan", "work"))
        self.assertEqual(result.evidence_references[0]["reference"], "drive:acct-drive:f-hit")

    def test_repository_scope_does_not_query_external_sources(self) -> None:
        email = FakeEmail(accounts=[_gmail_account()], messages=[{
            "id": "m1", "subject": "secret", "snippet": "mailbox dump"
        }])
        drive = FakeDrive(accounts=[_drive_account()], files=[{
            "id": "f1", "account_id": "acct-drive", "name": "secret"
        }])
        calendar = FakeCalendar(accounts=[_calendar_account()], events=[{
            "id": "e1", "account_id": "acct-cal", "calendar_id": "primary", "summary": "secret"
        }])
        result = _resolver(email, calendar, drive).resolve(
            ContextRequest("secret", "work", scope="repository", repository_id="repo-a")
        )
        for source_id in ("gmail", "google_drive", "google_calendar"):
            self.assertNotIn(source_id, result.sources_queried)
        self.assertFalse(any(call[0] == "search_messages" for call in email.calls))
        self.assertFalse(any(call[0] == "search_files" for call in drive.calls))
        self.assertFalse(any(call[0] == "search_events" for call in calendar.calls))

    def test_all_repositories_scope_keeps_external_sources_unavailable(self) -> None:
        email = FakeEmail(accounts=[_gmail_account()])
        result = _resolver(email).resolve(ContextRequest("mail", "work", scope="all"))
        considered = {row["id"]: row for row in result.sources_considered}
        self.assertFalse(considered["gmail"]["available"])
        self.assertNotIn("gmail", result.sources_queried)


class ExternalSourceClimateIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.center = mock.Mock()
        self.center.repository_intelligence = None
        self.center.start_run.return_value = {
            "id": "run-ext",
            "status": "queued",
            "agent_id": "gemini",
            "model": "gemini-exact",
            "conversation_id": "conversation-ext",
            "repository_ids": [],
        }
        adapter = ClimateCodingAdapter(self.center)
        adapter.availability = mock.Mock(return_value={"id": "gemini", "state": "connected"})
        self.email = FakeEmail(
            accounts=[_gmail_account()],
            messages=[{
                "id": "m1",
                "thread_id": "t1",
                "subject": "Vendor invoice",
                "from_addr": "ap@example.com",
                "date_header": "Wed, 19 Aug 2026",
                "snippet": "invoice attached",
            }],
            bodies={"m1": "Please pay the vendor invoice."},
        )
        workspace = mock.Mock()
        workspace.preview.return_value = {"content": "selected", "binary": False}
        self.service = ClimateService(
            Registry([]),
            workspace,
            adapter,
            email_service=self.email,
        )

    def _run(self, **overrides):
        payload = {
            "provider": "gemini",
            "model": "gemini-exact",
            "prompt": "vendor invoice",
            "execution_mode": "climate_assisted",
            "context_scope": "general",
        }
        payload.update(overrides)
        return self.service.execute_chat("work", **payload)

    def test_general_airix_retrieves_gmail(self) -> None:
        result = self._run()
        self.assertIn("gmail", result["sources_used"])
        prompt = self.center.start_run.call_args.args[0]["prompt"]
        self.assertIn("vendor invoice", prompt.lower())
        self.assertIn("gmail:acct-mail:m1", prompt)

    def test_direct_mode_never_auto_queries_external_sources(self) -> None:
        result = self._run(execution_mode="direct")
        self.assertEqual(self.email.calls, [])
        self.assertEqual(result["sources_considered"], [])
        self.assertEqual(result["sources_used"], [])
        self.assertEqual(self.center.start_run.call_args.args[0]["prompt"], "vendor invoice")

    def test_persistence_records_provenance_and_failures(self) -> None:
        result = self._run()
        persisted = self.center.start_run.call_args.args[0]["climate_execution"]
        refs = {row["source_id"]: row for row in persisted["evidence_references"]}
        self.assertIn("gmail", persisted["sources_queried"])
        self.assertIn("gmail", persisted["sources_used"])
        self.assertEqual(refs["gmail"]["reference"], "gmail:acct-mail:m1")
        self.assertEqual(result["evidence_references"][0]["metadata"]["message_id"], "m1")
        self.assertIn("gmail", [row["id"] for row in result["sources_considered"]])


class NoWriteGuaranteeTests(unittest.TestCase):
    def test_drive_client_is_get_only(self) -> None:
        public = [name for name in dir(DriveClient) if not name.startswith("_")]
        self.assertEqual(set(public), {"list_files", "get_file", "export_text"})
        self.assertFalse(any(
            name in public
            for name in ("create", "update", "delete", "patch", "upload", "copy")
        ))

    def test_drive_service_rejects_write_actions(self) -> None:
        service = DriveService(store=mock.Mock(), email_service=mock.Mock(), drive_client=mock.Mock())
        for action in FORBIDDEN_DRIVE_ACTIONS:
            with self.assertRaises(DriveServiceError):
                service.assert_not_write_action(action)

    def test_gmail_source_never_loads_threads_or_writes(self) -> None:
        email = FakeEmail(
            accounts=[_gmail_account()],
            messages=[{
                "id": "m1",
                "subject": "Hello",
                "from_addr": "a@example.com",
                "snippet": "Hello",
            }],
        )
        source = GmailContextSource(email)
        request = ContextRequest("Hello", "work")
        found = source.search(request, limit=4)
        source.retrieve(request, found, char_budget=400)
        self.assertFalse(any(call[0] == "get_thread" for call in email.calls))
        for action in FORBIDDEN_GMAIL_ACTIONS:
            self.assertFalse(any(call[0] == action for call in email.calls))

    def test_drive_search_never_lists_whole_drive(self) -> None:
        self.assertEqual(_drive_search_query(""), "")
        self.assertIn("name contains", _drive_search_query("PMNP plan"))
        client = mock.Mock()
        service = DriveService(store=mock.Mock(), email_service=mock.Mock(), drive_client=client)
        service.connected_accounts = mock.Mock(return_value=[_drive_account()])  # type: ignore[method-assign]
        self.assertEqual(service.search_files("work", q="  "), [])
        client.list_files.assert_not_called()

    def test_drive_export_is_capped(self) -> None:
        client = DriveClient(http_get=lambda *args, **kwargs: None)
        resp = mock.Mock()
        resp.status_code = 200
        resp.content = ("Z" * 20_000).encode("utf-8")
        client._raw_get = mock.Mock(return_value=resp)  # noqa: SLF001
        text = client.export_text("token", "file-1", max_chars=500)
        self.assertLessEqual(len(text), MAX_EXPORT_CHARS)
        self.assertEqual(len(text), 500)

    def test_incremental_drive_oauth_keeps_existing_scopes(self) -> None:
        scopes = google_api_scopes_for_account(
            {"has_calendar": True, "has_gmail": True, "scopes": ""},
            extra=DRIVE_SCOPES,
        )
        joined = " ".join(scopes)
        self.assertIn("gmail.readonly", joined)
        self.assertIn("calendar.events.readonly", joined)
        self.assertIn("drive.readonly", joined)


class Phase1RegressionTests(unittest.TestCase):
    def test_internal_sources_remain_registered_and_ranked(self) -> None:
        defaults = _resolver()
        self.assertEqual(
            [source.id for source in defaults.registry.sources()],
            [
                "repositories", "tasks", "notebook_notes", "sql_workspace",
                "repository_activity", "gmail", "google_drive", "google_calendar",
                "dhis2_environment", "dhis2_uid_index", "dhis2_enrichment",
                "dhis2_explorer", "dhis2_reports", "dhis2_operations",
            ],
        )
        self.assertEqual(defaults.max_candidates_per_source, 12)
        self.assertEqual(defaults.max_evidence, 8)
        self.assertEqual(defaults.max_chars, 12_000)
        self.assertEqual(defaults.max_item_chars, 2_400)

        notebook = mock.Mock()
        notebook.status_counts.return_value = {}
        notebook.search.return_value = [{
            "id": "task-1",
            "note_type": "task",
            "title": "Quarterly PMNP plan",
            "body_md": "nutrition targets",
            "status": "open",
            "priority": "high",
            "updated_at": "2026-08-19",
            "repositories": [],
        }]
        result = build_default_context_resolver(
            registry=Registry([]),
            repository_workspace=mock.Mock(),
            notebook_store=notebook,
            sql_workspace_store=None,
            intelligence_loader=lambda: None,
        ).resolve(ContextRequest("PMNP plan", "work"))
        self.assertIn("tasks", result.sources_used)
        self.assertIn("note:task-1", result.packet)
        self.assertTrue(result.packet.startswith("CLIMATE context packet"))


if __name__ == "__main__":
    unittest.main()
