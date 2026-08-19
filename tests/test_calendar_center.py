"""Focused tests for Calendar Center (shared Google OAuth + readonly Calendar)."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlparse

from hub.calendar.api import CalendarClient, parse_event
from hub.calendar.convert import convert_event_to_notebook
from hub.calendar.service import CalendarService, CalendarServiceError
from hub.email.crypto import redact_account_public
from hub.email.db import EmailDatabase
from hub.email.models import (
    CALENDAR_SCOPES,
    GMAIL_SCOPES,
    has_calendar_scopes,
    has_gmail_scopes,
    merge_scope_strings,
)
from hub.email.oauth import build_authorization_url
from hub.email.service import EmailService
from hub.email.settings_gmail import GmailOAuthSettings
from hub.email.store import EmailStore
from hub.notebook.db import NotebookDatabase
from hub.notebook.store import NotebookStore


def _oauth_settings() -> GmailOAuthSettings:
    return GmailOAuthSettings(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://127.0.0.1:8080/email/oauth/callback",
        enabled=True,
    )


def _connected_account(store: EmailStore, *, workspace: str = "personal", scopes: str = "") -> dict:
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    scope = scopes or merge_scope_strings(" ".join(GMAIL_SCOPES), " ".join(CALENDAR_SCOPES))
    return store.upsert_connected_account(
        workspace=workspace,
        email="me@example.com",
        google_sub="sub-1",
        token_payload={"refresh_token": "rt", "access_token": "at", "scope": scope},
        access_expires_at=future,
        scopes=scope,
    )


class IncrementalOAuthTests(unittest.TestCase):
    def test_calendar_scopes_in_auth_url(self) -> None:
        # Direct Calendar scopes still get identity scopes; Calendar Center
        # start requests Gmail+Calendar together (covered in merge test).
        url = build_authorization_url(
            _oauth_settings(), state="s1", scopes=CALENDAR_SCOPES
        )
        qs = parse_qs(urlparse(url).query)
        scope = qs["scope"][0]
        self.assertIn("calendar.calendarlist.readonly", scope)
        self.assertIn("calendar.events.readonly", scope)
        self.assertIn("openid", scope)
        self.assertNotIn("gmail.modify", scope)
        self.assertEqual(qs["include_granted_scopes"], ["true"])

    def test_incremental_calendar_oauth_merges_scopes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EmailStore(EmailDatabase(Path(tmp.name) / "email.db"), secret_key="sec")
        email = EmailService(store, oauth_settings=_oauth_settings())
        cal = CalendarService(store, email_service=email)
        # Existing gmail-only account
        acct = store.upsert_connected_account(
            workspace="personal",
            email="me@example.com",
            google_sub="sub-1",
            token_payload={"refresh_token": "rt", "scope": " ".join(GMAIL_SCOPES)},
            access_expires_at=None,
            scopes=" ".join(GMAIL_SCOPES),
        )
        started = cal.start_calendar_oauth(workspace="personal", account_id=acct["id"])
        # Auth URL must include both Gmail and Calendar so Gmail is not dropped.
        from urllib.parse import parse_qs, urlparse

        scope = parse_qs(urlparse(started["authorization_url"]).query)["scope"][0]
        self.assertIn("gmail.readonly", scope)
        self.assertIn("calendar.events.readonly", scope)

        def fake_post(url, data=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "access_token": "access-new",
                "expires_in": 3600,
                # Google often returns only newly granted scopes in the response.
                "scope": " ".join(CALENDAR_SCOPES),
            }
            return resp

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            resp.json.return_value = {"email": "me@example.com", "sub": "sub-1"}
            return resp

        email._http_post = fake_post
        email._http_get = fake_get
        updated = email.complete_oauth(state=started["state"], code="code")
        self.assertEqual(updated["id"], acct["id"])
        self.assertTrue(updated["has_gmail"])
        # Token response only listed Calendar scopes; prior Gmail scopes are preserved.
        self.assertTrue(updated["has_calendar"])
        self.assertTrue(has_calendar_scopes(updated["scopes"]))
        self.assertTrue(has_gmail_scopes(updated["scopes"]))
        # Refresh token preserved when Google omits it on incremental grant.
        payload = store.get_token_payload(updated["id"])
        self.assertEqual(payload["refresh_token"], "rt")

    def test_requested_scopes_not_stored_without_google_grant(self) -> None:
        """Requested Calendar must not mark has_calendar if Google didn't grant it."""
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EmailStore(EmailDatabase(Path(tmp.name) / "email.db"), secret_key="sec")
        email = EmailService(store, oauth_settings=_oauth_settings())
        acct = store.upsert_connected_account(
            workspace="personal",
            email="me@example.com",
            google_sub="sub-1",
            token_payload={"refresh_token": "rt", "scope": " ".join(GMAIL_SCOPES)},
            access_expires_at=None,
            scopes=" ".join(GMAIL_SCOPES),
        )
        started = email.start_oauth(
            workspace="personal",
            account_id=acct["id"],
            scopes=(*GMAIL_SCOPES, *CALENDAR_SCOPES),
        )

        def fake_post(url, data=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            # Google returned only Gmail — user denied Calendar on consent.
            resp.json.return_value = {
                "access_token": "access-new",
                "expires_in": 3600,
                "scope": "openid email profile " + " ".join(GMAIL_SCOPES),
            }
            return resp

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            resp.json.return_value = {"email": "me@example.com", "sub": "sub-1"}
            return resp

        email._http_post = fake_post
        email._http_get = fake_get
        updated = email.complete_oauth(state=started["state"], code="code")
        self.assertTrue(updated["has_gmail"])
        self.assertFalse(updated["has_calendar"])


class WorkspaceFilterTests(unittest.TestCase):
    def test_personal_work_filtering(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EmailStore(EmailDatabase(Path(tmp.name) / "e.db"), secret_key="sec")
        email = EmailService(store, oauth_settings=_oauth_settings())
        cal = CalendarService(store, email_service=email)
        _connected_account(store, workspace="personal")
        store.upsert_connected_account(
            workspace="work",
            email="work@example.com",
            google_sub="sub-2",
            token_payload={"refresh_token": "r", "access_token": "a"},
            access_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            scopes=merge_scope_strings(" ".join(GMAIL_SCOPES), " ".join(CALENDAR_SCOPES)),
        )
        self.assertEqual(len(cal.list_accounts("personal")), 1)
        self.assertEqual(len(cal.list_accounts("work")), 1)
        self.assertEqual(cal.list_accounts("personal")[0]["email"], "me@example.com")


class CalendarListingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "e.db"), secret_key="sec"
        )
        self.acct = _connected_account(self.store, workspace="work")
        self.email = EmailService(self.store, oauth_settings=_oauth_settings())

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            if "calendarList" in url:
                resp.json.return_value = {
                    "items": [
                        {
                            "id": "primary",
                            "summary": "Primary",
                            "primary": True,
                            "timeZone": "UTC",
                        }
                    ]
                }
            elif "/events/" in url and url.rstrip("/").split("/")[-1] != "events":
                resp.json.return_value = {
                    "id": "ev1",
                    "summary": "Standup",
                    "description": "Daily",
                    "location": "Room A",
                    "hangoutLink": "https://meet.google.com/abc",
                    "htmlLink": "https://calendar.google.com/event?eid=1",
                    "start": {"dateTime": "2026-07-26T09:00:00Z", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-07-26T09:30:00Z", "timeZone": "UTC"},
                    "attendees": [{"email": "a@x.com", "responseStatus": "accepted"}],
                    "recurringEventId": "recur-1",
                }
            elif "/events" in url:
                resp.json.return_value = {
                    "items": [
                        {
                            "id": "ev1",
                            "summary": "Standup",
                            "start": {"dateTime": "2026-07-26T09:00:00Z"},
                            "end": {"dateTime": "2026-07-26T09:30:00Z"},
                            "recurringEventId": "recur-1",
                        },
                        {
                            "id": "ev2",
                            "summary": "Holiday",
                            "start": {"date": "2026-07-27"},
                            "end": {"date": "2026-07-28"},
                        },
                    ],
                    "nextPageToken": "p2",
                }
            else:
                resp.json.return_value = {}
            return resp

        self.cal = CalendarService(
            self.store,
            email_service=self.email,
            calendar_client=CalendarClient(http_get=fake_get),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_calendars_and_events_listing(self) -> None:
        cals = self.cal.list_calendars(self.acct["id"], force_refresh=True)
        self.assertEqual(cals["calendars"][0]["id"], "primary")
        listing = self.cal.list_events(
            self.acct["id"],
            view="week",
            calendar_id="primary",
            q="Standup",
            force_refresh=True,
        )
        self.assertEqual(len(listing["events"]), 2)
        self.assertTrue(any(e["all_day"] for e in listing["events"]))
        self.assertTrue(any(e.get("recurring_event_id") for e in listing["events"]))
        self.assertEqual(listing["next_page_token"], "p2")
        self.assertIn("Standup", listing["q"])

    def test_event_detail_fields(self) -> None:
        detail = self.cal.get_event(self.acct["id"], "primary", "ev1", force_refresh=True)
        ev = detail["event"]
        self.assertEqual(ev["summary"], "Standup")
        self.assertEqual(ev["location"], "Room A")
        self.assertTrue(ev["hangout_link"])
        self.assertEqual(ev["attendees"][0]["email"], "a@x.com")
        self.assertEqual(ev["calendar_summary"], "Primary")


class MissingScopeAndApiErrorTests(unittest.TestCase):
    def test_missing_calendar_scopes(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EmailStore(EmailDatabase(Path(tmp.name) / "e.db"), secret_key="sec")
        email = EmailService(store, oauth_settings=_oauth_settings())
        cal = CalendarService(store, email_service=email)
        acct = store.upsert_connected_account(
            workspace="personal",
            email="a@x.com",
            google_sub="g",
            token_payload={"refresh_token": "r", "access_token": "a"},
            access_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            scopes=" ".join(GMAIL_SCOPES),
        )
        with self.assertRaises(CalendarServiceError) as ctx:
            cal.list_calendars(acct["id"])
        self.assertEqual(ctx.exception.code, "missing_scopes")

    def test_rate_limit(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EmailStore(EmailDatabase(Path(tmp.name) / "e.db"), secret_key="sec")
        email = EmailService(store, oauth_settings=_oauth_settings())
        acct = _connected_account(store)

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 429
            resp.content = b"{}"
            resp.json.return_value = {"error": {"message": "quota"}}
            return resp

        cal = CalendarService(
            store, email_service=email, calendar_client=CalendarClient(http_get=fake_get)
        )
        with self.assertRaises(CalendarServiceError):
            cal.list_calendars(acct["id"], force_refresh=True)
        self.assertEqual(store.get_account(acct["id"])["status"], "error")


class ConvertAndRedactionTests(unittest.TestCase):
    def test_convert_and_repo_link(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        notes = NotebookStore(NotebookDatabase(Path(tmp.name) / "n.db"))
        event = {
            "id": "ev1",
            "calendar_id": "primary",
            "calendar_summary": "Primary",
            "summary": "Planning",
            "description": "Agenda",
            "location": "HQ",
            "hangout_link": "https://meet.google.com/x",
            "html_link": "https://calendar.google.com/event?eid=1",
            "all_day": False,
            "start": {"date_time": "2026-07-26T10:00:00Z", "date": None},
            "end": {"date_time": "2026-07-26T11:00:00Z", "date": None},
            "attendees": [],
            "recurring_event_id": "",
        }
        note = convert_event_to_notebook(
            notes, event=event, workspace="personal", note_type="note"
        )
        self.assertEqual(note["scope"], "personal")
        self.assertIn("from-calendar", note["tags"])
        task = convert_event_to_notebook(
            notes,
            event=event,
            workspace="work",
            note_type="task",
            repository_id="live-processing",
            repository_label="LP",
        )
        self.assertEqual(task["note_type"], "task")
        self.assertTrue(any(r["repository_id"] == "live-processing" for r in task["repositories"]))

    def test_token_redaction(self) -> None:
        public = redact_account_public(
            {"id": "1", "email": "a@x.com", "token_encrypted": "CIPHER", "refresh_token": "nope"}
        )
        dumped = json.dumps(public)
        self.assertNotIn("CIPHER", dumped)
        self.assertNotIn("nope", dumped)


class CalendarRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        os.environ["CENTRAL_HUB_EMAIL_DATABASE"] = str(root / "email.db")
        os.environ["CENTRAL_HUB_SECRET_KEY"] = "cal-route-secret"
        os.environ["GMAIL_CLIENT_ID"] = "cid"
        os.environ["GMAIL_CLIENT_SECRET"] = "csecret"
        os.environ["GMAIL_REDIRECT_URI"] = "http://127.0.0.1:8080/email/oauth/callback"
        for key in ("DHIS2_BASE_URL", "DHIS2_USERNAME", "DHIS2_PASSWORD"):
            os.environ.pop(key, None)

        import hub.settings as settings_mod
        import app as app_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        cls.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(root / "notebook.db"))
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_nav_and_pages(self) -> None:
        r = self.client.get("/personal/calendar")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Calendar", html)
        self.assertIn("calendar.events.readonly", html)
        self.assertIn("cal-shell", html)
        self.assertIn("fullcalendar", html.lower())
        self.assertNotIn("csecret", html)

        r2 = self.client.get("/work/calendar")
        self.assertEqual(r2.status_code, 200)
        self.assertIn("Work Calendar", r2.get_data(as_text=True))

        r3 = self.client.get("/system/google-connections")
        self.assertEqual(r3.status_code, 200)
        body = r3.get_data(as_text=True)
        self.assertIn("Google Connections", body)
        self.assertIn("Connect (Calendar)", body)
        self.assertIn("Connect (Drive)", body)
        self.assertIn("drive.readonly", body)

    def test_calendar_oauth_start(self) -> None:
        r = self.client.get("/email/oauth/calendar/start?workspace=personal")
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get("Location", "")
        self.assertIn("accounts.google.com", loc)
        self.assertIn("calendar.events.readonly", loc)
        self.assertIn("gmail.readonly", loc)

    def test_drive_oauth_start(self) -> None:
        r = self.client.get("/email/oauth/drive/start?workspace=personal")
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get("Location", "")
        self.assertIn("accounts.google.com", loc)
        self.assertIn("drive.readonly", loc)
        self.assertIn("gmail.readonly", loc)

    def test_personal_dashboard_upcoming_section(self) -> None:
        r = self.client.get("/personal")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Upcoming Personal Events", html)
        self.assertIn("Calendar", html)


if __name__ == "__main__":
    unittest.main()
