"""Official References — subject detection + library tests."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from app import create_app
from hub.notebook.db import NotebookDatabase
from hub.notebook.references import (
    OfficialReferencesStore,
    ReferenceError,
    infer_type_from_text,
    infer_year_from_text,
    title_from_filename,
)
from hub.notebook.store import NotebookStore
from hub.notebook.subject_detect import detect_subject_from_text
from hub.notebook.text_extract import extract_text_from_bytes
from werkzeug.datastructures import FileStorage


class InferenceTests(unittest.TestCase):
    def test_infer_year_and_type_from_filename(self) -> None:
        self.assertEqual(infer_year_from_text("DM-2026-014-Health.pdf"), 2026)
        self.assertEqual(infer_type_from_text("DM-2026-014-Health.pdf"), "department_memorandum")
        self.assertEqual(infer_type_from_text("Advisory_on_Reporting.docx"), "advisory")
        self.assertEqual(title_from_filename("DM-2026-014-Health.pdf"), "DM 2026 014 Health")


class SubjectDetectUnitTests(unittest.TestCase):
    def test_explicit_subject_line(self) -> None:
        text = (
            "Republic of Example\n"
            "Department of Health\n"
            "SUBJECT: Cold Chain Monitoring Update\n"
            "Body of the memorandum follows.\n"
        )
        result = detect_subject_from_text(text)
        self.assertEqual(result["subject"], "Cold Chain Monitoring Update")
        self.assertEqual(result["subject_source"], "detected")
        self.assertEqual(result["confidence"], "high")

    def test_explicit_re_line(self) -> None:
        result = detect_subject_from_text("Re: Quarterly reporting reminder\nPlease note…\n")
        self.assertEqual(result["subject"], "Quarterly reporting reminder")
        self.assertEqual(result["subject_source"], "detected")

    def test_subject_label_next_line(self) -> None:
        text = "SUBJECT:\nImplementation of New Guidelines\n\nDetails here.\n"
        result = detect_subject_from_text(text)
        self.assertEqual(result["subject"], "Implementation of New Guidelines")
        self.assertEqual(result["subject_source"], "detected")

    def test_fallback_suggestion_from_heading(self) -> None:
        text = (
            "Department of Health\n"
            "Memorandum\n"
            "National Immunization Program Adjustments for 2026\n"
            "This document outlines the adjustments.\n"
        )
        result = detect_subject_from_text(text)
        self.assertEqual(result["subject_source"], "suggested")
        self.assertEqual(result["confidence"], "medium")
        self.assertIn("Immunization", result["subject"] or "")

    def test_empty_text_no_subject(self) -> None:
        result = detect_subject_from_text("   \n\n")
        self.assertIsNone(result["subject"])
        self.assertEqual(result["confidence"], "none")


class TextExtractUnitTests(unittest.TestCase):
    def test_txt_extract(self) -> None:
        data = b"SUBJECT: Sample Subject\nHello world.\n"
        out = extract_text_from_bytes(data, filename="memo.txt")
        self.assertTrue(out["ok"])
        self.assertIn("SUBJECT: Sample Subject", out["text"])

    def test_unsupported_type(self) -> None:
        out = extract_text_from_bytes(b"\xff\xd8\xff", filename="scan.jpg")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "unsupported_type")

    def test_empty_or_scanned_pdf(self) -> None:
        # Minimal PDF with no extractable text stream → empty_or_scanned.
        pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
        out = extract_text_from_bytes(pdf, filename="blank.pdf")
        self.assertFalse(out["ok"])
        self.assertEqual(out["reason"], "empty_or_scanned")


class OfficialReferencesStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.db = NotebookDatabase(root / "notebook.db")
        self.store = OfficialReferencesStore(self.db, root=root / "work-notebook" / "references")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _upload(self, name: str, data: bytes = b"%PDF-1.4 test") -> FileStorage:
        return FileStorage(stream=io.BytesIO(data), filename=name, content_type="application/octet-stream")

    def test_migration_010_applied(self) -> None:
        applied = self.db.applied_migrations()
        self.assertIn("009_official_references", applied)
        self.assertIn("010_official_references_subject", applied)

    def test_create_file_auto_metadata_and_year_path(self) -> None:
        item = self.store.create(
            title="",
            ref_type="department_memorandum",
            year=None,
            upload=self._upload("DM-2026-014-Health.pdf"),
        )
        self.assertEqual(item["year"], 2026)
        self.assertEqual(item["ref_type"], "department_memorandum")
        self.assertEqual(item["storage_kind"], "file")
        self.assertTrue(item["title"])
        self.assertTrue(item["relative_path"].startswith("2026/"))
        self.assertTrue(item["created_at"])
        disk = self.store.resolve_file(item["id"])
        self.assertTrue(disk.is_file())
        self.assertEqual(disk.parent.name, "2026")

    def test_create_detects_subject_from_txt(self) -> None:
        body = b"Department Memorandum\nSUBJECT: Cold Chain Alert\nDetails follow.\n"
        item = self.store.create(
            title="",
            ref_type="department_memorandum",
            year=2026,
            upload=self._upload("DM-2026-Subject.txt", body),
        )
        self.assertEqual(item["subject"], "Cold Chain Alert")
        self.assertEqual(item["subject_source"], "detected")

    def test_create_suggests_subject_without_explicit_line(self) -> None:
        body = (
            b"Department of Health\n"
            b"Memorandum\n"
            b"Expanded Program on Immunization Reporting Changes\n"
            b"Please implement the following changes immediately.\n"
        )
        item = self.store.create(
            title="EPI memo",
            ref_type="department_memorandum",
            year=2026,
            upload=self._upload("memo-2026.txt", body),
        )
        self.assertEqual(item["subject_source"], "suggested")
        self.assertTrue(item["subject"])

    def test_unsupported_upload_leaves_subject_null(self) -> None:
        item = self.store.create(
            title="Scan",
            ref_type="other",
            year=2025,
            upload=self._upload("scan-2025.jpg", b"\xff\xd8\xff\xe0fake"),
        )
        self.assertIsNone(item["subject"])
        self.assertEqual(item["subject_source"], "")

    def test_legacy_null_subject_valid(self) -> None:
        item = self.store.create(
            title="Link only legacy",
            ref_type="guideline",
            year=2024,
            external_url="https://example.com/guide",
        )
        self.assertIsNone(item["subject"])
        self.assertEqual(item["subject_source"], "")
        # Explicit clear stays null.
        updated = self.store.update(item["id"], {"subject": "", "subject_source": "manual"})
        self.assertIsNone(updated["subject"])

    def test_manual_subject_and_search(self) -> None:
        item = self.store.create(
            title="Other memo",
            ref_type="other",
            year=2024,
            upload=self._upload("other-2024.pdf"),
            subject="Vaccine refrigerator logs",
            subject_source="manual",
        )
        self.assertEqual(item["subject"], "Vaccine refrigerator logs")
        found = self.store.list(q="refrigerator")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["id"], item["id"])

    def test_suggest_from_upload_quick_add(self) -> None:
        data = b"RE: Facility Reporting Deadline Extension\nBody\n"
        meta = self.store.suggest_from_upload(self._upload("advisory-2026.txt", data))
        self.assertEqual(meta["subject"], "Facility Reporting Deadline Extension")
        self.assertEqual(meta["subject_source"], "detected")
        self.assertEqual(meta["confidence"], "high")
        self.assertEqual(meta["year"], 2026)

    def test_create_external_link_only(self) -> None:
        item = self.store.create(
            title="Online guideline",
            ref_type="guideline",
            year=2025,
            external_url="https://example.com/guide",
        )
        self.assertEqual(item["storage_kind"], "link")
        self.assertEqual(item["external_url"], "https://example.com/guide")
        self.assertFalse(item["has_file"])

    def test_file_plus_source_url(self) -> None:
        item = self.store.create(
            title="Memo with source",
            ref_type="department_memorandum",
            year=2024,
            upload=self._upload("memo-2024.pdf"),
            source_url="https://example.com/source",
        )
        self.assertEqual(item["storage_kind"], "file")
        self.assertTrue(item["has_source"])
        self.assertEqual(item["source_url"], "https://example.com/source")

    def test_file_and_external_link(self) -> None:
        item = self.store.create(
            title="Hybrid",
            ref_type="other",
            year=2024,
            upload=self._upload("doc-2024.pdf"),
            external_url="https://example.com/mirror",
        )
        self.assertEqual(item["storage_kind"], "file_and_link")

    def test_requires_file_or_link(self) -> None:
        with self.assertRaises(ReferenceError) as ctx:
            self.store.create(title="Empty", ref_type="other", year=2024)
        self.assertEqual(ctx.exception.code, "file_or_link_required")

    def test_grouped_by_year_then_type(self) -> None:
        self.store.create(
            title="Memo 2026",
            ref_type="department_memorandum",
            year=2026,
            upload=self._upload("a-2026.pdf"),
        )
        self.store.create(
            title="Guide 2026",
            ref_type="guideline",
            year=2026,
            upload=self._upload("b-2026.pdf"),
        )
        self.store.create(
            title="Adv 2025",
            ref_type="advisory",
            year=2025,
            upload=self._upload("c-2025.pdf"),
        )
        groups = self.store.grouped()
        self.assertEqual([g["year"] for g in groups], [2026, 2025])
        types_2026 = [t["type"] for t in groups[0]["types"]]
        self.assertEqual(types_2026[0], "department_memorandum")
        self.assertIn("guideline", types_2026)

    def test_search_and_filters(self) -> None:
        self.store.create(
            title="Cold chain advisory",
            ref_type="advisory",
            year=2025,
            short_note="Stage guidance",
            upload=self._upload("adv-2025.pdf"),
        )
        self.store.create(
            title="Other memo",
            ref_type="department_memorandum",
            year=2024,
            upload=self._upload("memo-2024.pdf"),
        )
        found = self.store.list(q="cold")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["title"], "Cold chain advisory")
        by_year = self.store.list(year=2024)
        self.assertEqual(len(by_year), 1)
        by_type = self.store.list(ref_type="advisory")
        self.assertEqual(len(by_type), 1)

    def test_update_and_delete(self) -> None:
        item = self.store.create(
            title="Temp",
            ref_type="other",
            year=2024,
            upload=self._upload("temp-2024.pdf"),
        )
        path = self.store.resolve_file(item["id"])
        self.assertTrue(path.exists())
        updated = self.store.update(
            item["id"],
            {"title": "Updated", "ref_type": "department_memorandum", "year": 2024},
        )
        self.assertEqual(updated["title"], "Updated")
        self.assertTrue(self.store.delete(item["id"]))
        self.assertFalse(path.exists())
        self.assertIsNone(self.store.get(item["id"]))

    def test_replace_file_redetects_subject(self) -> None:
        item = self.store.create(
            title="Temp",
            ref_type="other",
            year=2024,
            upload=self._upload("temp-2024.txt", b"No subject here just fluff text enough.\n"),
        )
        updated = self.store.update(
            item["id"],
            {
                "upload": self._upload(
                    "temp-2024b.txt",
                    b"SUBJECT: Replacement Subject Line\nBody\n",
                )
            },
        )
        self.assertEqual(updated["subject"], "Replacement Subject Line")
        self.assertEqual(updated["subject_source"], "detected")


class OfficialReferencesRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.app = create_app()
        self.app.config["TESTING"] = True
        nb_db = NotebookDatabase(root / "notebook.db")
        self.app.config["NOTEBOOK"] = NotebookStore(nb_db)
        self.refs_root = root / "work-notebook" / "references"
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_references_view_work_only(self) -> None:
        resp = self.client.get("/work/notebook?view=references")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Official References", resp.data)
        resp_p = self.client.get("/personal/notebook?view=references")
        self.assertEqual(resp_p.status_code, 200)
        self.assertNotIn(b"nb-refs-shell", resp_p.data)

    def test_detect_meta_api_autofill_payload(self) -> None:
        data = {
            "file": (io.BytesIO(b"SUBJECT: Quick Add Subject Detect\nMore text.\n"), "DM-2026-QA.txt"),
        }
        resp = self.client.post(
            "/api/notebook/references/detect-meta",
            data=data,
            content_type="multipart/form-data",
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["subject"], "Quick Add Subject Detect")
        self.assertEqual(payload["subject_source"], "detected")
        self.assertEqual(payload["confidence"], "high")
        self.assertEqual(payload["year"], 2026)

    def test_create_via_form_shows_subject(self) -> None:
        import app as app_module

        real = app_module.OfficialReferencesStore

        def _factory(db, root=None):
            return real(db, root=self.refs_root)

        app_module.OfficialReferencesStore = _factory  # type: ignore[misc,assignment]
        try:
            store = OfficialReferencesStore(
                self.app.config["NOTEBOOK"].db, root=self.refs_root
            )
            item = store.create(
                title="DM 2026 Sample",
                ref_type="department_memorandum",
                year=2026,
                short_note="Test note",
                source_url="https://example.com/src",
                subject="Visible Subject Line",
                subject_source="manual",
                upload=FileStorage(
                    stream=io.BytesIO(b"%PDF-1.4 route"),
                    filename="DM-2026-Sample.pdf",
                    content_type="application/pdf",
                ),
            )
            resp = self.client.get("/work/notebook?view=references&year=2026")
            self.assertEqual(resp.status_code, 200)
            self.assertIn(b"DM 2026 Sample", resp.data)
            self.assertIn(b"Visible Subject Line", resp.data)
            self.assertIn(b"Source", resp.data)
            file_resp = self.client.get(f"/work/notebook/references/{item['id']}/file")
            self.assertEqual(file_resp.status_code, 200)
        finally:
            app_module.OfficialReferencesStore = real


if __name__ == "__main__":
    unittest.main()
