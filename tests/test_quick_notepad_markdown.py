"""Focused tests: Quick Notepad Markdown Edit/Preview (no Bold/Strike toolbar)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hub.notebook.db import NotebookDatabase
from hub.notebook.markdown_util import render_markdown
from hub.notebook.notepad import QuickNotepadStore
from hub.notebook.store import NotebookStore


class MarkdownPreviewTests(unittest.TestCase):
    def test_preview_renders_typed_markdown_subset(self) -> None:
        html = render_markdown("hello **world** and ~~gone~~")
        self.assertIn("<strong>world</strong>", html)
        self.assertIn("<del>gone</del>", html)

    def test_preview_panel_must_not_use_nb_md_grid(self) -> None:
        """Regression: .nb-md is a 2-column editor grid; must not wrap QN preview."""
        panel = (
            Path(__file__).resolve().parents[1]
            / "templates"
            / "partials"
            / "quick_notepad_panel.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="qn-preview"', panel)
        self.assertNotIn("nb-md", panel)
        self.assertNotIn("qn-bold", panel)
        self.assertNotIn("qn-strike", panel)
        css = (
            Path(__file__).resolve().parents[1] / "static" / "css" / "style.css"
        ).read_text(encoding="utf-8")
        block = css.split(".qn-preview {", 1)[1].split("}", 1)[0]
        self.assertNotIn("grid-template", block)


class QuickNotepadMarkdownApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "notebook.db"
        import os

        os.environ["NOTEBOOK_DATABASE"] = str(self.db_path)
        from app import create_app

        self.app = create_app()
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_preview_api_renders_markdown(self) -> None:
        r = self.client.post(
            "/api/notebook/preview",
            data=json.dumps({"markdown": "Keep **bold** and ~~strike~~"}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("<strong>bold</strong>", data["html"])
        self.assertIn("<del>strike</del>", data["html"])

    def test_autosave_preserves_markdown_markers(self) -> None:
        body = "Keep **bold** and ~~strike~~"
        put = self.client.put(
            "/api/notebook/notepad?scope=personal",
            data=json.dumps(
                {
                    "scope": "personal",
                    "content": body,
                    "content_format": "markdown",
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(put.get_json()["ok"])
        pad = put.get_json()["notepad"]
        self.assertEqual(pad["content"], body)
        self.assertEqual(pad["content_format"], "markdown")

        got = self.client.get("/api/notebook/notepad?scope=personal")
        self.assertEqual(got.get_json()["notepad"]["content"], body)

        conv = self.client.post(
            "/api/notebook/notepad/convert",
            data=json.dumps({"scope": "personal"}),
            content_type="application/json",
        )
        self.assertTrue(conv.get_json()["ok"])
        note_id = conv.get_json()["note_id"]
        note = self.app.config["NOTEBOOK"].get(note_id)
        assert note is not None
        self.assertIn("**bold**", note["body_md"])
        self.assertIn("~~strike~~", note["body_md"])

    def test_ui_has_edit_preview_not_bold_strike(self) -> None:
        page = self.client.get("/personal")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertNotIn('id="qn-bold"', html)
        self.assertNotIn('id="qn-strike"', html)
        self.assertIn('id="qn-mode-preview"', html)
        self.assertIn('id="qn-preview"', html)
        self.assertIn("<strong>Preview</strong>", html)

        js = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "quick_notepad.js"
        ).read_text(encoding="utf-8")
        self.assertNotIn("wrapSelection", js)
        self.assertNotIn("applyWrap", js)
        self.assertNotIn("qn-bold", js)
        self.assertIn("setViewMode", js)
        self.assertIn("/api/notebook/preview", js)

    def test_plain_text_unchanged_by_markdown_renderer_path(self) -> None:
        put = self.client.put(
            "/api/notebook/notepad?scope=work",
            data=json.dumps(
                {
                    "scope": "work",
                    "content": "**still plain**",
                    "content_format": "plain",
                }
            ),
            content_type="application/json",
        )
        pad = put.get_json()["notepad"]
        self.assertEqual(pad["content_format"], "plain")
        self.assertEqual(pad["content"], "**still plain**")


class StoreMarkdownRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = NotebookDatabase(Path(self.tmp.name) / "n.db")
        self.pad = QuickNotepadStore(self.db, scope="personal")
        self.notes = NotebookStore(self.db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_revision_keeps_markers_after_clear(self) -> None:
        body = "**a** ~~b~~"
        self.pad.save(content=body, content_format="markdown")
        cleared = self.pad.clear()
        self.assertTrue(cleared["revisions"])
        self.assertIn("**a**", cleared["revisions"][0]["content"])


if __name__ == "__main__":
    unittest.main()
