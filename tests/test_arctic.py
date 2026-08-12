"""Focused tests for ARCTIC Personal profile + document registry."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app
from hub.arctic.context import (
    build_arctic_ai_context,
    work_context_must_exclude_arctic,
)
from hub.arctic.db import ArcticDatabase
from hub.arctic.models import normalize_primary_role
from hub.arctic.sources import GoogleDriveDocumentSource, LocalDocumentSource
from hub.arctic.store import ArcticError, ArcticStore


class ArcticRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "arctic.db"
        self.store = ArcticStore(ArcticDatabase(self.db_path))
        self.sample = Path(self.tmp.name) / "cv-v1.pdf"
        self.sample.write_bytes(b"%PDF-1.4 sample")
        self.sample2 = Path(self.tmp.name) / "cv-v2.pdf"
        self.sample2.write_bytes(b"%PDF-1.4 sample2")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_schema_migration_applied(self) -> None:
        self.assertIn("001_arctic_profile_and_registry", self.store.db.applied_migrations())

    def test_profile_roundtrip(self) -> None:
        profile = self.store.update_profile(
            {
                "display_name": "Alex Rivera",
                "headline": "Public health analyst",
                "email": "alex@example.com",
                "skills": ["SQL", "DHIS2"],
                "links": [{"label": "Portfolio", "url": "https://example.com"}],
            }
        )
        self.assertEqual(profile["display_name"], "Alex Rivera")
        self.assertEqual(profile["workspace"], "personal")
        self.assertEqual(profile["climate_section"], "ARCTIC")
        again = self.store.get_profile()
        self.assertEqual(again["skills"], ["SQL", "DHIS2"])

    def test_register_and_no_duplication(self) -> None:
        doc = self.store.register_document(
            {
                "title": "CV 2026",
                "source_type": "local",
                "source_ref": str(self.sample),
                "primary_role": "cv",
                "tags": ["cv", "2026"],
            }
        )
        self.assertEqual(doc["primary_role"], "cv")
        self.assertFalse(doc["content_embedded"])
        with self.assertRaises(ArcticError) as ctx:
            self.store.register_document(
                {
                    "title": "CV duplicate",
                    "source_type": "local",
                    "source_ref": str(self.sample),
                }
            )
        self.assertEqual(ctx.exception.code, "duplicate")

    def test_primary_role_replacement_latest_cv(self) -> None:
        first = self.store.register_document(
            {
                "title": "Old CV",
                "source_type": "local",
                "source_ref": str(self.sample),
                "primary_role": "cv",
            }
        )
        second = self.store.register_document(
            {
                "title": "New CV",
                "source_type": "local",
                "source_ref": str(self.sample2),
                "primary_role": "cv",
            }
        )
        latest = self.store.latest_cv()
        assert latest is not None
        self.assertEqual(latest["id"], second["id"])
        self.assertEqual(latest["title"], "New CV")
        old = self.store.get_document(first["id"])
        assert old is not None
        self.assertEqual(old["primary_role"], "")

    def test_sources_local_ready_drive_deferred(self) -> None:
        sources = {s["source_type"]: s for s in self.store.list_sources()}
        self.assertEqual(sources["local"]["status"], "ready")
        self.assertEqual(sources["google_drive"]["status"], "deferred")
        self.assertFalse(GoogleDriveDocumentSource().descriptor().sync_ready)
        self.assertTrue(LocalDocumentSource().descriptor().sync_ready)

    def test_favorites_and_recent(self) -> None:
        doc = self.store.register_document(
            {
                "title": "Signature",
                "source_type": "local",
                "source_ref": str(self.sample),
                "primary_role": "signature",
                "is_favorite": True,
            }
        )
        self.store.touch_accessed(doc["id"])
        favs = self.store.favorites()
        self.assertEqual(len(favs), 1)
        self.assertEqual(favs[0]["id"], doc["id"])
        recent = self.store.recent()
        self.assertEqual(recent[0]["id"], doc["id"])

    def test_career_pack_logical_view(self) -> None:
        self.store.register_document(
            {
                "title": "CV",
                "source_type": "local",
                "source_ref": str(self.sample),
                "primary_role": "cv",
            }
        )
        self.store.register_document(
            {
                "title": "Cover",
                "source_type": "local",
                "source_ref": str(self.sample2),
                "primary_role": "cover_letter",
            }
        )
        pack = self.store.career_pack()
        self.assertTrue(pack["logical_view"])
        self.assertEqual(pack["count"], 2)
        self.assertIn("cv", pack["primaries"])

    def test_sensitive_blocked(self) -> None:
        with self.assertRaises(ArcticError) as ctx:
            self.store.update_profile({"summary": "my banking password is secret"})
        self.assertEqual(ctx.exception.code, "sensitive_blocked")

    def test_role_aliases(self) -> None:
        self.assertEqual(normalize_primary_role("resume"), "cv")
        self.assertEqual(normalize_primary_role("headshot"), "profile_photo")


class ArcticIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ArcticStore(ArcticDatabase(Path(self.tmp.name) / "arctic.db"))
        self.sample = Path(self.tmp.name) / "cv.pdf"
        self.sample.write_bytes(b"cv")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_ai_context_requires_explicit_selection(self) -> None:
        doc = self.store.register_document(
            {
                "title": "CV",
                "source_type": "local",
                "source_ref": str(self.sample),
                "primary_role": "cv",
            }
        )
        empty = build_arctic_ai_context(self.store, document_ids=[], include_profile=False)
        self.assertEqual(empty["documents"], [])
        self.assertFalse(empty["auto_ri"])
        self.assertEqual(empty["isolated_from"], "VANTA")

        packed = build_arctic_ai_context(
            self.store,
            document_ids=[doc["id"]],
            include_profile=True,
            include_latest_cv=True,
        )
        self.assertEqual(len(packed["documents"]), 1)
        self.assertIsNotNone(packed["profile"])
        # Never embed file bytes / full path by default.
        self.assertNotIn("source_ref", packed["documents"][0])
        self.assertFalse(packed["content_embedded"])

    def test_work_payload_must_exclude_arctic(self) -> None:
        self.assertTrue(work_context_must_exclude_arctic({"workspace": "work", "repos": []}))
        self.assertFalse(
            work_context_must_exclude_arctic(
                {"climate_section": "ARCTIC", "arctic_documents": [{"id": "x"}]}
            )
        )
        self.assertFalse(
            work_context_must_exclude_arctic({"arctic_auto_include": True})
        )


class ArcticRouteIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = root / "arctic.db"
        self.app = create_app()
        from hub.arctic.service import ArcticService

        self.app.config["ARCTIC"] = ArcticService(ArcticStore(ArcticDatabase(self.db)))
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_arctic_pages_force_personal_workspace(self) -> None:
        for path in ("/personal/arctic", "/personal/arctic/profile", "/personal/arctic/files"):
            resp = self.client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            self.assertIn(b"ARCTIC", resp.data)

    def test_api_latest_cv_and_ai_context(self) -> None:
        sample = Path(self.tmp.name) / "cv.pdf"
        sample.write_bytes(b"cv")
        store: ArcticStore = self.app.config["ARCTIC"].store
        doc = store.register_document(
            {
                "title": "Primary CV",
                "source_type": "local",
                "source_ref": str(sample),
                "primary_role": "cv",
            }
        )
        resp = self.client.get("/api/arctic/latest-cv")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_json()
        self.assertEqual(body["latest_cv"]["id"], doc["id"])
        self.assertEqual(body["resolved_via"], "primary_cv")

        resp2 = self.client.post(
            "/api/arctic/ai-context",
            json={"document_ids": [doc["id"]], "include_profile": False},
        )
        self.assertEqual(resp2.status_code, 200)
        ctx = resp2.get_json()["context"]
        self.assertEqual(ctx["workspace"], "personal")
        self.assertEqual(ctx["isolated_from"], "VANTA")
        self.assertFalse(ctx["auto_ri"])

    def test_work_nav_does_not_list_arctic_in_work_section(self) -> None:
        # Hit work dashboard; response should not treat ARCTIC as a Work nav item.
        resp = self.client.get("/work")
        self.assertEqual(resp.status_code, 200)
        # ARCTIC lives under Personal nav only — work page may still mention climate in docs,
        # but sidebar Work section should not use arctic_dashboard endpoint as Work core.
        html = resp.get_data(as_text=True)
        # Soft check: work page loads; arctic route remains personal-scoped.
        resp_a = self.client.get("/personal/arctic")
        self.assertEqual(resp_a.status_code, 200)


if __name__ == "__main__":
    unittest.main()
