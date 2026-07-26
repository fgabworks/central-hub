"""Focused tests for Email Center (Gmail readonly OAuth)."""

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

from hub.email.crypto import decrypt_token_blob, encrypt_token_blob, redact_account_public
from hub.email.db import EmailDatabase
from hub.email.gmail_api import GmailApiError, GmailClient, parse_message_detail, parse_message_summary
from hub.email.oauth import (
    OAuthError,
    build_authorization_url,
    exchange_code,
    refresh_access_token,
)
from hub.email.service import EmailService, EmailServiceError
from hub.email.settings_gmail import GmailOAuthSettings
from hub.email.store import EmailStore
from hub.email.convert import convert_message_to_notebook
from hub.notebook.db import NotebookDatabase
from hub.notebook.store import NotebookStore


def _oauth_settings() -> GmailOAuthSettings:
    return GmailOAuthSettings(
        client_id="test-client-id",
        client_secret="test-client-secret",
        redirect_uri="http://127.0.0.1:8080/email/oauth/callback",
        enabled=True,
    )


class CryptoAndRedactionTests(unittest.TestCase):
    def test_encrypt_decrypt_roundtrip(self) -> None:
        blob = encrypt_token_blob("secret-key", {"refresh_token": "rt-1", "access_token": "at-1"})
        self.assertNotIn("rt-1", blob)
        data = decrypt_token_blob("secret-key", blob)
        self.assertEqual(data["refresh_token"], "rt-1")

    def test_redact_account_public(self) -> None:
        public = redact_account_public(
            {
                "id": "a1",
                "email": "a@example.com",
                "token_encrypted": "CIPHERTEXT",
                "refresh_token": "should-not-appear",
                "status": "connected",
            }
        )
        self.assertNotIn("token_encrypted", public)
        self.assertNotIn("refresh_token", public)
        self.assertTrue(public["token_stored"])
        dumped = json.dumps(public)
        self.assertNotIn("CIPHERTEXT", dumped)
        self.assertNotIn("should-not-appear", dumped)


class OAuthStateAndCallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "email.db"),
            secret_key="test-secret",
        )
        self.service = EmailService(self.store, oauth_settings=_oauth_settings())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_authorization_url_readonly_scope(self) -> None:
        url = build_authorization_url(_oauth_settings(), state="abc")
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        self.assertEqual(qs["state"], ["abc"])
        self.assertIn("gmail.readonly", qs["scope"][0])
        self.assertIn("openid", qs["scope"][0])
        self.assertIn("email", qs["scope"][0])
        self.assertNotIn("gmail.modify", qs["scope"][0])
        self.assertEqual(qs["access_type"], ["offline"])

    def test_oauth_state_single_use_and_expiry(self) -> None:
        self.store.create_oauth_state(workspace="personal", state="st1")
        peeked = self.store.get_oauth_state("st1")
        self.assertIsNotNone(peeked)
        first = self.store.consume_oauth_state("st1")
        self.assertIsNotNone(first)
        self.assertEqual(first["workspace"], "personal")
        self.assertIsNone(self.store.consume_oauth_state("st1"))

        self.store.create_oauth_state(workspace="work", state="st2", ttl_seconds=-1)
        self.assertIsNone(self.store.get_oauth_state("st2"))
        self.assertIsNone(self.store.consume_oauth_state("st2"))

    def test_callback_rejects_invalid_state(self) -> None:
        with self.assertRaises(EmailServiceError) as ctx:
            self.service.complete_oauth(state="nope", code="code")
        self.assertEqual(ctx.exception.code, "invalid_state")
        self.assertIn("Connect again", str(ctx.exception))
    def test_callback_success_assigns_workspace(self) -> None:
        started = self.service.start_oauth(workspace="personal")
        state = started["state"]

        def fake_post(url, data=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            if "token" in url:
                resp.json.return_value = {
                    "access_token": "access-1",
                    "refresh_token": "refresh-1",
                    "expires_in": 3600,
                    "scope": "https://www.googleapis.com/auth/gmail.readonly",
                }
            else:
                resp.json.return_value = {}
            return resp

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            if "userinfo" in url:
                resp.json.return_value = {"email": "me@example.com", "sub": "sub-1"}
            else:
                resp.json.return_value = {}
            return resp

        self.service._http_post = fake_post
        self.service._http_get = fake_get
        account = self.service.complete_oauth(state=state, code="auth-code")
        self.assertEqual(account["workspace"], "personal")
        self.assertEqual(account["email"], "me@example.com")
        self.assertNotIn("refresh_token", account)
        self.assertNotIn("token_encrypted", account)
        payload = self.store.get_token_payload(account["id"])
        self.assertEqual(payload["refresh_token"], "refresh-1")


class AccountAssignmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "email.db"),
            secret_key="test-secret",
        )
        self.service = EmailService(self.store, oauth_settings=_oauth_settings())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_assign_personal_work(self) -> None:
        acct = self.store.upsert_connected_account(
            workspace="work",
            email="a@x.com",
            google_sub="g1",
            token_payload={"refresh_token": "r"},
            access_expires_at=None,
            scopes="gmail.readonly",
        )
        moved = self.service.assign_workspace(acct["id"], "personal")
        self.assertEqual(moved["workspace"], "personal")
        self.assertEqual(len(self.service.list_accounts("personal")), 1)
        self.assertEqual(len(self.service.list_accounts("work")), 0)


class TokenRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "email.db"),
            secret_key="test-secret",
        )
        self.service = EmailService(self.store, oauth_settings=_oauth_settings())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_expired_token_refresh(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        acct = self.store.upsert_connected_account(
            workspace="work",
            email="a@x.com",
            google_sub="g1",
            token_payload={"refresh_token": "refresh-old", "access_token": "stale"},
            access_expires_at=past,
            scopes="gmail.readonly",
        )

        def fake_post(url, data=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "access_token": "access-new",
                "expires_in": 3600,
            }
            return resp

        self.service._http_post = fake_post
        token = self.service._access_token(acct["id"])
        self.assertEqual(token, "access-new")
        payload = self.store.get_token_payload(acct["id"])
        self.assertEqual(payload["access_token"], "access-new")
        self.assertEqual(payload["refresh_token"], "refresh-old")

    def test_revoked_refresh_marks_needs_reauth(self) -> None:
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        acct = self.store.upsert_connected_account(
            workspace="work",
            email="a@x.com",
            google_sub="g1",
            token_payload={"refresh_token": "bad"},
            access_expires_at=past,
            scopes="gmail.readonly",
        )

        def fake_post(url, data=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 400
            resp.json.return_value = {"error": "invalid_grant"}
            return resp

        self.service._http_post = fake_post
        with self.assertRaises(EmailServiceError) as ctx:
            self.service._access_token(acct["id"])
        self.assertEqual(ctx.exception.code, "needs_reauth")
        updated = self.store.get_account(acct["id"])
        self.assertEqual(updated["status"], "needs_reauth")


class MessageListingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "email.db"),
            secret_key="test-secret",
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.acct = self.store.upsert_connected_account(
            workspace="work",
            email="a@x.com",
            google_sub="g1",
            token_payload={"refresh_token": "r", "access_token": "access"},
            access_expires_at=future,
            scopes="gmail.readonly",
        )
        self.calls: list[str] = []

        def fake_get(url, headers=None, params=None, timeout=None):
            self.calls.append(url)
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            if url.endswith("/messages") and "attachments" not in url:
                resp.json.return_value = {
                    "messages": [{"id": "m1", "threadId": "t1"}],
                    "nextPageToken": "page-2",
                    "resultSizeEstimate": 2,
                }
            elif "/messages/m1" in url and "attachments" not in url:
                resp.json.return_value = {
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "Hello",
                    "labelIds": ["INBOX", "UNREAD"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Subj"},
                            {"name": "From", "value": "x@y.com"},
                            {"name": "To", "value": "a@x.com"},
                            {"name": "Date", "value": "Fri, 25 Jul 2026"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": ""},
                    },
                }
            elif url.endswith("/labels"):
                resp.json.return_value = {
                    "labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]
                }
            else:
                resp.json.return_value = {}
            return resp

        client = GmailClient(http_get=fake_get)
        self.service = EmailService(
            self.store,
            oauth_settings=_oauth_settings(),
            gmail_client=client,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_list_search_pagination(self) -> None:
        result = self.service.list_messages(
            self.acct["id"], view="inbox", q="invoice", force_refresh=True
        )
        self.assertEqual(len(result["messages"]), 1)
        self.assertEqual(result["messages"][0]["subject"], "Subj")
        self.assertTrue(result["messages"][0]["is_unread"])
        self.assertIn("UNREAD", result["messages"][0]["label_ids"])
        self.assertEqual(result["next_page_token"], "page-2")
        self.assertIn("invoice", result["query"])
        self.assertFalse(result["from_cache"])
        cached = self.service.list_messages(self.acct["id"], view="inbox", q="invoice")
        self.assertTrue(cached["from_cache"])
        self.assertTrue(cached["messages"][0]["is_unread"])

    def test_thread_loading(self) -> None:
        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            if "/threads/" in url:
                resp.json.return_value = {
                    "id": "t1",
                    "messages": [
                        {
                            "id": "m1",
                            "threadId": "t1",
                            "snippet": "A",
                            "labelIds": ["INBOX"],
                            "payload": {
                                "headers": [{"name": "Subject", "value": "T"}],
                                "mimeType": "text/plain",
                                "body": {
                                    "data": "SGVsbG8="  # Hello
                                },
                            },
                        }
                    ],
                }
            else:
                resp.json.return_value = {}
            return resp

        self.service.gmail = GmailClient(http_get=fake_get)
        thread = self.service.get_thread(self.acct["id"], "t1")
        self.assertEqual(len(thread["messages"]), 1)
        self.assertIn("Hello", thread["messages"][0]["body_text"])


class AttachmentSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "email.db"),
            secret_key="test-secret",
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.acct = self.store.upsert_connected_account(
            workspace="personal",
            email="a@x.com",
            google_sub="g1",
            token_payload={"refresh_token": "r", "access_token": "access"},
            access_expires_at=future,
            scopes="gmail.readonly",
        )

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            if "/attachments/" in url:
                # "hi" base64url
                resp.json.return_value = {"data": "aGk="}
            elif "/messages/" in url:
                resp.json.return_value = {
                    "id": "m1",
                    "threadId": "t1",
                    "snippet": "",
                    "labelIds": [],
                    "payload": {
                        "headers": [{"name": "Subject", "value": "S"}],
                        "filename": "doc.txt",
                        "mimeType": "text/plain",
                        "body": {"attachmentId": "att-1", "size": 2},
                        "parts": [],
                    },
                }
            else:
                resp.json.return_value = {}
            return resp

        self.service = EmailService(
            self.store,
            oauth_settings=_oauth_settings(),
            gmail_client=GmailClient(http_get=fake_get),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_attachment_must_belong_to_message(self) -> None:
        with self.assertRaises(EmailServiceError):
            self.service.download_attachment(self.acct["id"], "m1", "wrong-att")
        content, filename, mime = self.service.download_attachment(
            self.acct["id"], "m1", "att-1"
        )
        self.assertEqual(content, b"hi")
        self.assertEqual(filename, "doc.txt")
        self.assertEqual(mime, "text/plain")


class ConvertAndLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.notes = NotebookStore(NotebookDatabase(Path(self.tmp.name) / "notes.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_convert_note_task_and_repo_link(self) -> None:
        message = {
            "id": "m1",
            "thread_id": "t1",
            "subject": "Invoice due",
            "from_addr": "billing@x.com",
            "to_addr": "me@x.com",
            "date_header": "today",
            "body_text": "Please pay",
            "snippet": "Please pay",
        }
        note = convert_message_to_notebook(
            self.notes,
            message=message,
            workspace="personal",
            account_email="me@x.com",
            note_type="note",
        )
        self.assertEqual(note["scope"], "personal")
        self.assertIn("from-email", note["tags"])
        self.assertEqual(note["repositories"], [])

        task = convert_message_to_notebook(
            self.notes,
            message=message,
            workspace="work",
            note_type="task",
            repository_id="live-processing",
            repository_label="Live Processing",
        )
        self.assertEqual(task["note_type"], "task")
        self.assertEqual(task["scope"], "work")
        self.assertTrue(any(r["repository_id"] == "live-processing" for r in task["repositories"]))


class RevokedAndApiErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = EmailStore(
            EmailDatabase(Path(self.tmp.name) / "email.db"),
            secret_key="test-secret",
        )
        self.acct = self.store.upsert_connected_account(
            workspace="work",
            email="a@x.com",
            google_sub="g1",
            token_payload={"refresh_token": "r", "access_token": "a"},
            access_expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            scopes="gmail.readonly",
        )
        self.service = EmailService(self.store, oauth_settings=_oauth_settings())

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_revoked_account_unavailable(self) -> None:
        self.store.set_account_status(self.acct["id"], "revoked", clear_tokens=True)
        with self.assertRaises(EmailServiceError) as ctx:
            self.service.list_messages(self.acct["id"])
        self.assertEqual(ctx.exception.code, "account_unavailable")

    def test_rate_limit_error(self) -> None:
        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 429
            resp.content = b"{}"
            resp.json.return_value = {"error": {"message": "Rate limit"}}
            return resp

        self.service.gmail = GmailClient(http_get=fake_get)
        with self.assertRaises(EmailServiceError):
            self.service.list_messages(self.acct["id"], force_refresh=True)
        updated = self.store.get_account(self.acct["id"])
        self.assertEqual(updated["status"], "error")

    def test_gmail_api_401_needs_reauth(self) -> None:
        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 401
            resp.content = b"{}"
            resp.json.return_value = {"error": {"message": "Unauthorized"}}
            return resp

        self.service.gmail = GmailClient(http_get=fake_get)
        with self.assertRaises(EmailServiceError):
            self.service.list_messages(self.acct["id"], force_refresh=True)
        self.assertEqual(self.store.get_account(self.acct["id"])["status"], "needs_reauth")

    def test_forbidden_write_guard(self) -> None:
        with self.assertRaises(EmailServiceError):
            self.service.assert_not_write_action("send")


class EmailRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        os.environ["CENTRAL_HUB_EMAIL_DATABASE"] = str(root / "email.db")
        os.environ["CENTRAL_HUB_SECRET_KEY"] = "route-test-secret"
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

    def test_personal_and_work_email_pages(self) -> None:
        r = self.client.get("/personal/email")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Email Center", html)
        self.assertIn("gmail.readonly", html)
        self.assertIn("email-account-card", html)
        self.assertIn("Connect Account", html)
        self.assertIn("Manage Connections", html)
        self.assertNotIn("csecret", html)
        self.assertNotIn("token_encrypted", html)

        r2 = self.client.get("/work/email")
        self.assertEqual(r2.status_code, 200)
        work_html = r2.get_data(as_text=True)
        self.assertIn("Email Center", work_html)
        self.assertIn("Work email account", work_html)

    def test_oauth_start_redirects(self) -> None:
        r = self.client.get("/email/oauth/start?workspace=personal")
        self.assertEqual(r.status_code, 302)
        loc = r.headers.get("Location", "")
        self.assertIn("accounts.google.com", loc)
        self.assertIn("gmail.readonly", loc)

    def test_oauth_callback_invalid_state(self) -> None:
        r = self.client.get("/email/oauth/callback?state=bad&code=x")
        self.assertEqual(r.status_code, 302)

    def test_nav_includes_email_center(self) -> None:
        r = self.client.get("/personal")
        self.assertIn("Email Center", r.get_data(as_text=True))
        r2 = self.client.get("/work")
        self.assertIn("Email Center", r2.get_data(as_text=True))


class UnreadVisibilityTests(unittest.TestCase):
    def test_unread_label_detection(self) -> None:
        unread = parse_message_summary(
            {
                "id": "u1",
                "threadId": "t",
                "snippet": "Hi",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Unread mail"},
                        {"name": "From", "value": "a@x.com"},
                        {"name": "Date", "value": "Sat, 25 Jul 2026"},
                    ]
                },
            }
        )
        read = parse_message_summary(
            {
                "id": "r1",
                "threadId": "t",
                "snippet": "Bye",
                "labelIds": ["INBOX"],
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Read mail"},
                        {"name": "From", "value": "b@x.com"},
                        {"name": "Date", "value": "Sat, 25 Jul 2026"},
                    ]
                },
            }
        )
        self.assertTrue(unread["is_unread"])
        self.assertFalse(read["is_unread"])

    def test_mixed_list_search_pagination_preserves_status(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = EmailStore(EmailDatabase(Path(tmp.name) / "e.db"), secret_key="sec")
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        acct = store.upsert_connected_account(
            workspace="personal",
            email="me@example.com",
            google_sub="sub-u",
            token_payload={"refresh_token": "rt", "access_token": "at"},
            access_expires_at=future,
            scopes="gmail.readonly",
        )

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            if url.endswith("/messages") and "attachments" not in url:
                resp.json.return_value = {
                    "messages": [
                        {"id": "m-unread", "threadId": "t1"},
                        {"id": "m-read", "threadId": "t2"},
                    ],
                    "nextPageToken": "page-next",
                    "resultSizeEstimate": 2,
                }
            elif "/messages/m-unread" in url:
                resp.json.return_value = {
                    "id": "m-unread",
                    "threadId": "t1",
                    "snippet": "New note",
                    "labelIds": ["INBOX", "UNREAD", "STARRED"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Unread Subject"},
                            {"name": "From", "value": "new@x.com"},
                            {"name": "Date", "value": "Fri, 25 Jul 2026"},
                        ]
                    },
                }
            elif "/messages/m-read" in url:
                resp.json.return_value = {
                    "id": "m-read",
                    "threadId": "t2",
                    "snippet": "Old note",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Read Subject"},
                            {"name": "From", "value": "old@x.com"},
                            {"name": "Date", "value": "Thu, 24 Jul 2026"},
                        ]
                    },
                }
            else:
                resp.json.return_value = {}
            return resp

        service = EmailService(
            store,
            oauth_settings=_oauth_settings(),
            gmail_client=GmailClient(http_get=fake_get),
        )
        listing = service.list_messages(
            acct["id"], view="inbox", q="note", force_refresh=True
        )
        self.assertEqual(len(listing["messages"]), 2)
        by_id = {m["id"]: m for m in listing["messages"]}
        self.assertTrue(by_id["m-unread"]["is_unread"])
        self.assertFalse(by_id["m-read"]["is_unread"])
        self.assertEqual(listing["next_page_token"], "page-next")
        self.assertIn("note", listing["query"])

        # Pagination / search cache must keep unread flags.
        cached = service.list_messages(acct["id"], view="inbox", q="note")
        self.assertTrue(cached["from_cache"])
        cached_by_id = {m["id"]: m for m in cached["messages"]}
        self.assertTrue(cached_by_id["m-unread"]["is_unread"])
        self.assertFalse(cached_by_id["m-read"]["is_unread"])

        # Unread mailbox view still uses the same flag from labels.
        unread_view = service.list_messages(acct["id"], view="unread", force_refresh=True)
        self.assertTrue(any(m["is_unread"] for m in unread_view["messages"]))


class UnreadStyleRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        os.environ["CENTRAL_HUB_EMAIL_DATABASE"] = str(root / "email.db")
        os.environ["CENTRAL_HUB_SECRET_KEY"] = "unread-style-secret"
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
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        cls.acct = store.upsert_connected_account(
            workspace="personal",
            email="me@example.com",
            google_sub="sub-style",
            token_payload={"refresh_token": "rt", "access_token": "at"},
            access_expires_at=future,
            scopes="gmail.readonly",
        )

        def fake_get(url, headers=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.content = b"{}"
            if url.endswith("/messages") and "attachments" not in url:
                resp.json.return_value = {
                    "messages": [
                        {"id": "m-unread", "threadId": "t1"},
                        {"id": "m-read", "threadId": "t2"},
                    ],
                    "nextPageToken": "page-2",
                }
            elif "/messages/m-unread" in url:
                resp.json.return_value = {
                    "id": "m-unread",
                    "threadId": "t1",
                    "snippet": "New",
                    "labelIds": ["INBOX", "UNREAD"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Unread Subject"},
                            {"name": "From", "value": "new@x.com"},
                            {"name": "Date", "value": "Fri, 25 Jul 2026"},
                        ]
                    },
                }
            elif "/messages/m-read" in url:
                resp.json.return_value = {
                    "id": "m-read",
                    "threadId": "t2",
                    "snippet": "Old",
                    "labelIds": ["INBOX"],
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": "Read Subject"},
                            {"name": "From", "value": "old@x.com"},
                            {"name": "Date", "value": "Thu, 24 Jul 2026"},
                        ]
                    },
                }
            elif url.endswith("/labels"):
                resp.json.return_value = {"labels": [{"id": "INBOX", "name": "INBOX"}]}
            else:
                resp.json.return_value = {}
            return resp

        cls.app.config["EMAIL"].gmail = GmailClient(http_get=fake_get)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_unread_and_read_row_classes(self) -> None:
        r = self.client.get(
            f"/personal/email?account={self.acct['id']}&view=inbox&refresh=1"
        )
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("email-row is-unread", html)
        self.assertIn("email-row is-read", html)
        self.assertIn("email-unread-dot", html)
        self.assertIn("email-account-card", html)
        self.assertIn("Connect Account", html)
        self.assertIn("Manage Connections", html)
        self.assertIn("Unread Subject", html)
        self.assertIn("Read Subject", html)
        # Stylesheet distinguishes hover / selected / focus
        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".email-row.is-unread", css)
        self.assertIn(".email-row.is-read", css)
        self.assertIn(".email-row:hover", css)
        self.assertIn(".email-row.is-selected", css)
        self.assertIn(".email-row:focus-visible", css)
        self.assertIn("border-left-color", css)
        self.assertIn(".email-account-card", css)
        self.assertIn("grid-template-columns: minmax(220px, 280px) 1fr", css)

    def test_selected_state_and_no_auto_mark_read(self) -> None:
        r = self.client.get(
            f"/personal/email?account={self.acct['id']}&view=inbox&selected=m-unread&refresh=1"
        )
        html = r.get_data(as_text=True)
        self.assertIn("is-unread is-selected", html)
        # Opening the list must not imply write/mark-read capability.
        self.assertNotIn("mark as read", html.lower())
        self.assertIn("gmail.readonly", html.lower())

    def test_search_and_starred_views_keep_unread_markup(self) -> None:
        for view in ("inbox", "unread", "starred", "sent"):
            r = self.client.get(
                f"/personal/email?account={self.acct['id']}&view={view}&q=Subject&refresh=1"
            )
            self.assertEqual(r.status_code, 200, view)
            html = r.get_data(as_text=True)
            self.assertIn("is-unread", html, view)
            self.assertIn("is-read", html, view)


class EmailLayoutUiTests(unittest.TestCase):
    """Focused layout checks for the preview-style Email Center shell."""

    def test_mailbox_view_counts_helper(self) -> None:
        from hub.email.routes import _mailbox_view_counts

        counts = _mailbox_view_counts(
            [
                {"id": "INBOX", "messages_total": 128, "messages_unread": 23},
                {"id": "UNREAD", "messages_total": 23},
                {"id": "STARRED", "messages_total": 4},
                {"id": "SENT", "messages_total": 50},
            ]
        )
        self.assertEqual(counts["inbox"], 128)
        self.assertEqual(counts["unread"], 23)
        self.assertEqual(counts["starred"], 4)
        self.assertEqual(counts["sent"], 50)

    def test_center_template_has_account_card_and_main_list_hooks(self) -> None:
        html = (
            Path(__file__).resolve().parents[1] / "templates" / "email" / "center.html"
        ).read_text(encoding="utf-8")
        self.assertIn("email-account-card", html)
        self.assertIn("Connect Account", html)
        self.assertIn("Manage Connections", html)
        self.assertIn("Read-only access · Gmail API", html)
        self.assertIn("email-views", html)
        self.assertIn("email-search", html)
        self.assertIn("email-list", html)
        self.assertIn("email-unread-dot", html)
        self.assertIn("is-unread", html)
        self.assertIn("is-read", html)
        self.assertNotIn("email-account-actions", html)

    def test_responsive_account_card_stacks_above_list(self) -> None:
        css = (
            Path(__file__).resolve().parents[1] / "static" / "css" / "style.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".email-account-card", css)
        self.assertIn("order: -1", css)
        self.assertIn("@media (max-width: 900px)", css)


class ExchangeRefreshUnitTests(unittest.TestCase):
    def test_exchange_code_error(self) -> None:
        def fake_post(url, data=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 400
            resp.json.return_value = {"error": "invalid_grant", "error_description": "bad"}
            return resp

        with self.assertRaises(OAuthError):
            exchange_code(_oauth_settings(), "bad", http_post=fake_post)

    def test_refresh_preserves_refresh_token(self) -> None:
        def fake_post(url, data=None, params=None, timeout=None):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"access_token": "n", "expires_in": 10}
            return resp

        data = refresh_access_token(_oauth_settings(), "keep-me", http_post=fake_post)
        self.assertEqual(data["refresh_token"], "keep-me")


if __name__ == "__main__":
    unittest.main()
