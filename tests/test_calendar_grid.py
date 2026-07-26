"""Focused tests for Calendar grid UI helpers, JSON APIs, and sanitization."""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

from hub.calendar.api import CalendarClient, parse_event
from hub.calendar.drawer import drawer_actions, drawer_sections, format_event_when
from hub.calendar.fc_events import calendar_color_map, event_detail_payload, to_fullcalendar_event
from hub.calendar.sanitize import description_plain, sanitize_html
from hub.calendar.service import CalendarService, CalendarServiceError
from hub.email.db import EmailDatabase
from hub.email.models import CALENDAR_SCOPES, GMAIL_SCOPES, merge_scope_strings
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


def _connected(store: EmailStore, *, workspace: str = "personal") -> dict:
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    scope = merge_scope_strings(" ".join(GMAIL_SCOPES), " ".join(CALENDAR_SCOPES))
    return store.upsert_connected_account(
        workspace=workspace,
        email="me@example.com",
        google_sub="sub-grid",
        token_payload={"refresh_token": "rt", "access_token": "at", "scope": scope},
        access_expires_at=future,
        scopes=scope,
    )


class SanitizeTests(unittest.TestCase):
    def test_strips_script_and_keeps_safe_tags(self) -> None:
        raw = '<p>Hello <script>alert(1)</script><b>world</b></p><a href="https://x.test">x</a>'
        out = sanitize_html(raw)
        self.assertIn("<b>world</b>", out)
        self.assertIn('href="https://x.test"', out)
        self.assertNotIn("<script", out.lower())
        self.assertNotIn("alert(1)", out)

    def test_blocks_javascript_href(self) -> None:
        out = sanitize_html('<a href="javascript:alert(1)">x</a>')
        self.assertNotIn("javascript:", out.lower())

    def test_plain_text_escaped_with_breaks(self) -> None:
        out = sanitize_html("Line 1\nLine 2 & more")
        self.assertIn("Line 1<br>", out)
        self.assertIn("Line 2", out)
        self.assertIn("&amp;", out)

    def test_description_plain(self) -> None:
        self.assertEqual(description_plain("<p>Hi <b>there</b></p>"), "Hi there")

    def test_html_text_node_line_breaks_preserved(self) -> None:
        out = sanitize_html("<p>Line 1\nLine 2</p><br>Line 3")
        self.assertIn("Line 1<br>", out)
        self.assertIn("Line 2", out)
        self.assertIn("<br>", out)
        self.assertNotIn("<script", out.lower())


class DrawerViewTests(unittest.TestCase):
    def test_all_day_and_timed_when(self) -> None:
        all_day = {
            "all_day": True,
            "start": {"date": "2026-07-27"},
            "end": {"date": "2026-07-29"},
        }
        timed = {
            "all_day": False,
            "start": {"date_time": "2026-07-26T09:00:00Z", "time_zone": "UTC"},
            "end": {"date_time": "2026-07-26T10:00:00Z"},
        }
        self.assertIn("all day", format_event_when(all_day).lower())
        self.assertIn("2026-07-27", format_event_when(all_day))
        self.assertIn("→", format_event_when(all_day))
        self.assertIn("09:00", format_event_when(timed))

    def test_long_title_and_description_sections(self) -> None:
        long_title = "Planning " + ("sync " * 40)
        long_desc = "Agenda:\n" + ("- item\n" * 80) + '<script>bad()</script><b>OK</b>'
        detail = event_detail_payload(
            {
                "id": "long1",
                "calendar_id": "primary",
                "calendar_summary": "Primary",
                "summary": long_title,
                "description": long_desc,
                "location": "",
                "hangout_link": "",
                "html_link": "https://calendar.google.com/event?eid=1",
                "all_day": False,
                "start": {"date_time": "2026-07-26T10:00:00Z", "time_zone": "UTC"},
                "end": {"date_time": "2026-07-26T11:00:00Z"},
                "attendees": [],
                "recurring_event_id": "",
            },
            account={"id": "a1", "email": "me@example.com", "workspace": "personal"},
            display_time_zone="UTC",
        )
        self.assertEqual(detail["summary"], long_title)
        self.assertIn("<br>", detail["description_html"])
        self.assertIn("<b>OK</b>", detail["description_html"])
        self.assertNotIn("<script", detail["description_html"].lower())
        by_id = {s["id"]: s for s in detail["sections"]}
        self.assertTrue(by_id["location"]["empty"])
        self.assertTrue(by_id["attendees"]["empty"])
        self.assertTrue(by_id["meet"]["empty"])
        self.assertFalse(by_id["description"]["empty"])
        self.assertFalse(by_id["when"]["empty"])
        self.assertEqual(by_id["timezone"]["value"], "UTC")
        self.assertTrue(detail["actions"]["convert_note"])
        self.assertTrue(detail["actions"]["create_task"])
        self.assertFalse(detail["actions"]["link_repository"])
        self.assertTrue(detail["actions"]["open_in_google"])
        self.assertIn("edit", detail["actions"]["readonly_hidden"])

    def test_work_repo_actions_and_attendees_meet(self) -> None:
        detail = event_detail_payload(
            {
                "id": "w1",
                "calendar_id": "primary",
                "calendar_summary": "Work",
                "summary": "Review",
                "description": "",
                "location": "HQ",
                "hangout_link": "https://meet.google.com/abc",
                "html_link": "",
                "all_day": True,
                "start": {"date": "2026-08-01"},
                "end": {"date": "2026-08-02"},
                "attendees": [
                    {"email": "a@x.com", "display_name": "A", "response_status": "accepted"}
                ],
                "recurring_event_id": "recur-9",
            },
            account={"id": "a1", "email": "work@example.com", "workspace": "work"},
            registry_repos=[{"id": "live-processing", "label": "LP"}],
        )
        by_id = {s["id"]: s for s in detail["sections"]}
        self.assertFalse(by_id["location"]["empty"])
        self.assertFalse(by_id["attendees"]["empty"])
        self.assertFalse(by_id["meet"]["empty"])
        self.assertTrue(by_id["description"]["empty"])
        self.assertIn("recurring", by_id)
        self.assertTrue(detail["actions"]["link_repository"])
        self.assertFalse(detail["actions"]["open_in_google"])
        self.assertEqual(detail["actions"]["registry_repos"][0]["id"], "live-processing")

    def test_drawer_helpers_shared(self) -> None:
        sections = drawer_sections(
            {
                "all_day": False,
                "start": {"date_time": "2026-07-26T01:00:00Z", "time_zone": "Asia/Manila"},
                "end": {"date_time": "2026-07-26T02:00:00Z"},
                "calendar_summary": "P",
                "location": "",
                "attendees": [],
                "hangout_link": "",
                "description_html": "",
            },
            display_time_zone="Asia/Manila",
        )
        self.assertEqual(sections[2]["id"], "timezone")
        self.assertEqual(sections[2]["value"], "Asia/Manila")
        actions = drawer_actions({"html_link": "https://x"}, workspace="personal")
        self.assertFalse(actions["link_repository"])


class FullCalendarMappingTests(unittest.TestCase):
    def test_timed_event_placement(self) -> None:
        ev = parse_event(
            {
                "id": "t1",
                "summary": "Standup",
                "start": {"dateTime": "2026-07-26T09:00:00+08:00", "timeZone": "Asia/Manila"},
                "end": {"dateTime": "2026-07-26T09:30:00+08:00", "timeZone": "Asia/Manila"},
                "recurringEventId": "recur-1",
            },
            calendar={"id": "primary", "summary": "Primary", "backgroundColor": "#8b0000"},
        )
        colors = calendar_color_map(
            [{"id": "primary", "background_color": "#8b0000", "foreground_color": "#fff"}]
        )
        fc = to_fullcalendar_event(ev, calendar_colors=colors)
        self.assertEqual(fc["start"], "2026-07-26T09:00:00+08:00")
        self.assertEqual(fc["end"], "2026-07-26T09:30:00+08:00")
        self.assertFalse(fc["allDay"])
        self.assertFalse(fc["editable"])
        self.assertFalse(fc["startEditable"])
        self.assertFalse(fc["durationEditable"])
        self.assertEqual(fc["backgroundColor"], "#8b0000")
        self.assertEqual(fc["extendedProps"]["recurring_event_id"], "recur-1")

    def test_all_day_and_multi_day(self) -> None:
        ev = parse_event(
            {
                "id": "h1",
                "summary": "Holiday",
                "start": {"date": "2026-07-27"},
                "end": {"date": "2026-07-29"},
            },
            calendar={"id": "primary", "summary": "Primary"},
        )
        self.assertTrue(ev["all_day"])
        fc = to_fullcalendar_event(ev)
        self.assertTrue(fc["allDay"])
        self.assertEqual(fc["start"], "2026-07-27")
        # Google exclusive end date preserved for FullCalendar all-day
        self.assertEqual(fc["end"], "2026-07-29")

    def test_sanitized_description_in_fc_and_detail(self) -> None:
        ev = {
            "id": "e1",
            "calendar_id": "primary",
            "calendar_summary": "Primary",
            "summary": "X",
            "description": '<p>Ok</p><img src=x onerror=alert(1)><script>bad()</script>',
            "location": "HQ",
            "hangout_link": "https://meet.google.com/abc",
            "html_link": "",
            "all_day": False,
            "start": {"date_time": "2026-07-26T10:00:00Z", "date": None, "time_zone": "UTC"},
            "end": {"date_time": "2026-07-26T11:00:00Z", "date": None},
            "attendees": [{"email": "a@x.com", "display_name": "A", "response_status": "accepted"}],
            "recurring_event_id": "",
        }
        fc = to_fullcalendar_event(ev)
        self.assertIn("<p>Ok</p>", fc["extendedProps"]["description_html"])
        self.assertNotIn("<script", fc["extendedProps"]["description_html"].lower())
        self.assertNotIn("onerror", fc["extendedProps"]["description_html"].lower())
        detail = event_detail_payload(ev, account={"id": "a1", "email": "me@example.com"})
        self.assertTrue(detail["readonly"])
        self.assertTrue(detail["actions_disabled"]["create"])
        self.assertTrue(detail["actions_disabled"]["drag"])
        self.assertTrue(detail["actions_disabled"]["rsvp"])
        self.assertEqual(detail["hangout_link"], "https://meet.google.com/abc")
        self.assertNotIn("<script", detail["description_html"].lower())


class GridServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "email.db"), secret_key="sec"
        )
        self.email = EmailService(self.store, oauth_settings=_oauth_settings())
        self.acct = _connected(self.store)

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
                            "backgroundColor": "#8b0000",
                            "foregroundColor": "#ffffff",
                            "timeZone": "UTC",
                        },
                        {
                            "id": "work@example.com",
                            "summary": "Work",
                            "backgroundColor": "#0D5561",
                            "foregroundColor": "#ffffff",
                            "timeZone": "Asia/Manila",
                        },
                    ]
                }
            elif "/events/" in url and "/events?" not in url and not url.rstrip("/").endswith("events"):
                resp.json.return_value = {
                    "id": "ev1",
                    "summary": "Standup",
                    "description": "<b>Hi</b><script>x</script>",
                    "location": "Room A",
                    "hangoutLink": "https://meet.google.com/abc",
                    "start": {"dateTime": "2026-07-26T09:00:00Z", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-07-26T09:30:00Z", "timeZone": "UTC"},
                    "attendees": [{"email": "a@x.com", "responseStatus": "accepted"}],
                    "recurringEventId": "recur-1",
                }
            elif "/events" in url:
                cal = "work" if "work%40" in url or "work@" in url else "primary"
                items = []
                if cal == "primary" or "primary" in url:
                    items = [
                        {
                            "id": "ev1",
                            "summary": "Standup",
                            "start": {"dateTime": "2026-07-26T01:00:00Z"},
                            "end": {"dateTime": "2026-07-26T01:30:00Z"},
                            "recurringEventId": "recur-1",
                            "description": "<p>Daily</p><script>x</script>",
                        },
                        {
                            "id": "ev2",
                            "summary": "Holiday",
                            "start": {"date": "2026-07-27"},
                            "end": {"date": "2026-07-29"},
                        },
                    ]
                if "work" in url:
                    items = [
                        {
                            "id": "evw",
                            "summary": "Work sync",
                            "start": {"dateTime": "2026-07-28T03:00:00Z"},
                            "end": {"dateTime": "2026-07-28T04:00:00Z"},
                        }
                    ]
                q = (params or {}).get("q") or ""
                if q:
                    items = [i for i in items if q.lower() in (i.get("summary") or "").lower()]
                resp.json.return_value = {"items": items}
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

    def test_grid_range_month_placement_and_filters(self) -> None:
        payload = self.cal.list_events_for_grid(
            self.acct["id"],
            date_from="2026-07-01",
            date_to="2026-07-31",
            time_zone="UTC",
            force_refresh=True,
        )
        ids = {e["id"] for e in payload["events"]}
        self.assertIn("ev1", ids)
        self.assertIn("ev2", ids)
        self.assertIn("evw", ids)
        self.assertTrue(any(fc["allDay"] for fc in payload["fc_events"]))
        self.assertTrue(
            any(fc["extendedProps"].get("recurring_event_id") for fc in payload["fc_events"])
        )
        # Colors differ by calendar source
        colors = {fc["backgroundColor"] for fc in payload["fc_events"]}
        self.assertGreaterEqual(len(colors), 2)

        filtered = self.cal.list_events_for_grid(
            self.acct["id"],
            date_from="2026-07-01",
            date_to="2026-07-31",
            calendar_id="primary",
            q="Standup",
            time_zone="Asia/Manila",
            force_refresh=True,
        )
        self.assertEqual(filtered["time_zone"], "Asia/Manila")
        self.assertTrue(all(e["calendar_id"] == "primary" for e in filtered["events"]))
        self.assertTrue(all("Standup" in e["summary"] for e in filtered["events"]))

    def test_empty_range(self) -> None:
        payload = self.cal.list_events_for_grid(
            self.acct["id"],
            date_from="2025-01-01",
            date_to="2025-01-02",
            q="ZZZNOMATCH",
            force_refresh=True,
        )
        self.assertEqual(payload["fc_events"], [])


class GridRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        os.environ["CENTRAL_HUB_EMAIL_DATABASE"] = str(root / "email.db")
        os.environ["CENTRAL_HUB_SECRET_KEY"] = "cal-grid-secret"
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
        store: EmailStore = cls.app.config["EMAIL"].store
        cls.acct = _connected(store)

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
                            "backgroundColor": "#8b0000",
                            "timeZone": "UTC",
                        }
                    ]
                }
            elif "/events/" in url and not url.rstrip("/").endswith("events"):
                resp.json.return_value = {
                    "id": "ev1",
                    "summary": "Standup",
                    "description": "<b>Safe</b><script>bad()</script>",
                    "location": "Room A",
                    "hangoutLink": "https://meet.google.com/abc",
                    "htmlLink": "https://calendar.google.com/event?eid=1",
                    "start": {"dateTime": "2026-07-26T09:00:00Z", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-07-26T09:30:00Z", "timeZone": "UTC"},
                    "attendees": [{"email": "a@x.com", "responseStatus": "accepted"}],
                }
            elif "/events" in url:
                resp.json.return_value = {
                    "items": [
                        {
                            "id": "ev1",
                            "summary": "Standup",
                            "start": {"dateTime": "2026-07-26T09:00:00Z"},
                            "end": {"dateTime": "2026-07-26T09:30:00Z"},
                        },
                        {
                            "id": "ev2",
                            "summary": "Holiday",
                            "start": {"date": "2026-07-27"},
                            "end": {"date": "2026-07-28"},
                        },
                    ]
                }
            else:
                resp.json.return_value = {}
            return resp

        cal: CalendarService = cls.app.config["CALENDAR"]
        cal.client = CalendarClient(http_get=fake_get)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_center_renders_grid_shell_and_disabled_writes(self) -> None:
        r = self.client.get(f"/personal/calendar?account={self.acct['id']}&view=month")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('id="calendar-grid"', html)
        self.assertIn("fullcalendar", html.lower())
        self.assertIn("calendar_center.js", html)
        self.assertIn('id="cal-drawer"', html)
        self.assertIn("cal-drawer-panel", html)
        self.assertIn("cal-drawer-title", html)
        self.assertIn("Today", html)
        self.assertIn("Create event", html)
        self.assertIn("disabled", html)
        self.assertIn("drag / resize / rsvp", html.lower())
        self.assertIn("CentralHubCalendar.init", html)
        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )
        self.assertIn("justify-content: flex-end", css)
        self.assertIn(".cal-drawer-body", css)
        self.assertIn("overflow-y: auto", css)
        self.assertIn("position: sticky", css)
        self.assertIn("@media (max-width: 720px)", css)
        js = (Path(__file__).resolve().parents[1] / "static" / "js" / "calendar_center.js").read_text(
            encoding="utf-8"
        )
        self.assertIn('e.key === "Escape"', js)
        self.assertIn("Convert to Note", js)
        self.assertIn("Create Task", js)
        self.assertIn("Link Repository", js)
        self.assertIn("Open in Google", js)
        self.assertIn("cal-drawer-section", js)
        self.assertIn("Read-only", js)
        # Month view must not force every event into solid bars (timed + multi-day fuse).
        self.assertIn('eventDisplay: "auto"', js)
        self.assertIn('copy.display = copy.allDay ? "block" : "list-item"', js)
        self.assertIn("dayGridMonth", js)
        self.assertIn("fc-daygrid-dot-event", css)
        self.assertIn("text-overflow: ellipsis", css)
        # Regression: clamping daygrid events clips multi-day week-boundary titles.
        self.assertNotIn("max-width: calc(100% - 4px)", css)
        self.assertIn("fc-daygrid-body", css)
        self.assertIn("overflow: hidden", css)

    def test_events_api_ok_empty_and_error(self) -> None:
        ok = self.client.get(
            f"/api/calendar/accounts/{self.acct['id']}/events"
            "?start=2026-07-01&end=2026-08-01&tz=UTC"
        )
        self.assertEqual(ok.status_code, 200)
        body = ok.get_json()
        self.assertTrue(body["ok"])
        self.assertGreaterEqual(len(body["events"]), 1)
        self.assertIn("allDay", body["events"][0])
        self.assertFalse(body["events"][0]["editable"])

        missing = self.client.get(
            f"/api/calendar/accounts/{self.acct['id']}/events?tz=UTC"
        )
        self.assertEqual(missing.status_code, 400)

        # Force API error path
        cal: CalendarService = self.app.config["CALENDAR"]
        old = cal.client

        def boom(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 500
            resp.content = b"{}"
            resp.json.return_value = {"error": {"message": "boom"}}
            return resp

        cal.client = CalendarClient(http_get=boom)
        try:
            err = self.client.get(
                f"/api/calendar/accounts/{self.acct['id']}/events"
                "?start=2026-07-01&end=2026-08-01&tz=UTC&refresh=1"
            )
            self.assertEqual(err.status_code, 400)
            self.assertFalse(err.get_json()["ok"])
        finally:
            cal.client = old

    def test_event_detail_api_sanitized(self) -> None:
        r = self.client.get(
            f"/api/calendar/accounts/{self.acct['id']}/calendars/primary/events/ev1?tz=UTC&refresh=1"
        )
        self.assertEqual(r.status_code, 200)
        ev = r.get_json()["event"]
        self.assertEqual(ev["summary"], "Standup")
        self.assertEqual(ev["location"], "Room A")
        self.assertIn("meet.google.com", ev["hangout_link"])
        self.assertIn("<b>Safe</b>", ev["description_html"])
        self.assertNotIn("<script", ev["description_html"].lower())
        self.assertTrue(ev["actions_disabled"]["edit"])
        self.assertTrue(ev["actions_disabled"]["delete"])
        self.assertTrue(ev["actions_disabled"]["rsvp"])
        self.assertIn("sections", ev)
        self.assertTrue(any(s["id"] == "when" for s in ev["sections"]))
        self.assertTrue(ev["actions"]["convert_note"])
        self.assertTrue(ev["actions"]["create_task"])
        # Personal account from fixture has workspace personal → no link repo unless work
        self.assertIn("link_repository", ev["actions"])

    def test_html_detail_page_sanitized(self) -> None:
        r = self.client.get(
            f"/calendar/accounts/{self.acct['id']}/calendars/primary/events/ev1?refresh=1"
        )
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("<b>Safe</b>", html)
        self.assertNotIn("<script>bad()", html)


if __name__ == "__main__":
    unittest.main()
