"""Quick Notepad Expand / Maximize / Minimize / Escape size modes."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from hub.notebook.db import NotebookDatabase
from hub.notebook.notepad import (
    QuickNotepadStore,
    normalize_panel_size,
)


class PanelSizeNormalizeTests(unittest.TestCase):
    def test_normalize_panel_size(self) -> None:
        self.assertEqual(normalize_panel_size("normal"), "normal")
        self.assertEqual(normalize_panel_size("Expanded"), "expanded")
        self.assertEqual(normalize_panel_size("MAXIMIZED"), "maximized")
        self.assertEqual(normalize_panel_size("nope"), "normal")
        self.assertEqual(normalize_panel_size(None), "normal")


class PanelSizeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = NotebookDatabase(Path(self.tmp.name) / "notebook.db")
        self.personal = QuickNotepadStore(self.db, scope="personal")
        self.work = QuickNotepadStore(self.db, scope="work")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migration_adds_panel_size(self) -> None:
        self.assertIn("006_notepad_panel_size", self.db.applied_migrations())
        pad = self.personal.get(include_revisions=False)
        self.assertEqual(pad["panel_size"], "normal")

    def test_persist_size_per_workspace(self) -> None:
        self.personal.save(panel_size="expanded", content="p")
        self.work.save(panel_size="maximized", content="w")
        self.assertEqual(self.personal.get()["panel_size"], "expanded")
        self.assertEqual(self.personal.get()["content"], "p")
        self.assertEqual(self.work.get()["panel_size"], "maximized")
        self.assertEqual(self.work.get()["content"], "w")

        bad = self.personal.save(panel_size="huge")
        self.assertEqual(bad["panel_size"], "normal")
        self.assertEqual(bad["content"], "p")

    def test_autosave_while_maximized_preserves_content(self) -> None:
        saved = self.work.save(
            content="cursor stays",
            content_format="markdown",
            panel_size="maximized",
            panel_open=True,
            panel_width=400,
        )
        self.assertEqual(saved["panel_size"], "maximized")
        self.assertEqual(saved["content"], "cursor stays")
        again = self.work.save(content="cursor stays and grows")
        self.assertEqual(again["panel_size"], "maximized")
        self.assertEqual(again["content"], "cursor stays and grows")
        self.assertEqual(again["panel_width"], 400)

    def test_minimize_path_persists_normal(self) -> None:
        self.personal.save(panel_size="expanded", content="keep")
        minimized = self.personal.save(panel_size="normal")
        self.assertEqual(minimized["panel_size"], "normal")
        self.assertEqual(minimized["content"], "keep")
        self.personal.save(panel_size="maximized")
        back = self.personal.save(panel_size="normal")
        self.assertEqual(back["panel_size"], "normal")
        self.assertEqual(back["content"], "keep")


class PanelSizeRouteAndUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        os.environ["NOTEBOOK_DATABASE"] = str(root / "notebook.db")
        for key in ("DHIS2_BASE_URL", "DHIS2_USERNAME", "DHIS2_PASSWORD"):
            os.environ.pop(key, None)

        import hub.settings as settings_mod
        import app as app_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app
        from hub.notebook.store import NotebookStore

        cls.app = create_app()
        cls.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(root / "notebook.db"))
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_api_persists_panel_size_and_isolates_scopes(self) -> None:
        personal = self.client.put(
            "/api/notebook/notepad?scope=personal",
            data=json.dumps(
                {
                    "scope": "personal",
                    "content": "personal draft",
                    "panel_size": "expanded",
                    "panel_open": True,
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(personal.get_json()["ok"])
        self.assertEqual(personal.get_json()["notepad"]["panel_size"], "expanded")

        work = self.client.put(
            "/api/notebook/notepad?scope=work",
            data=json.dumps(
                {
                    "scope": "work",
                    "content": "work draft",
                    "panel_size": "maximized",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(work.get_json()["notepad"]["panel_size"], "maximized")
        self.assertEqual(work.get_json()["notepad"]["content"], "work draft")

        got_p = self.client.get("/api/notebook/notepad?scope=personal").get_json()
        got_w = self.client.get("/api/notebook/notepad?scope=work").get_json()
        self.assertEqual(got_p["notepad"]["panel_size"], "expanded")
        self.assertEqual(got_p["notepad"]["content"], "personal draft")
        self.assertEqual(got_w["notepad"]["panel_size"], "maximized")
        self.assertNotEqual(got_p["notepad"]["content"], got_w["notepad"]["content"])

    def test_autosave_while_maximized_via_api(self) -> None:
        r = self.client.put(
            "/api/notebook/notepad?scope=work",
            data=json.dumps(
                {
                    "scope": "work",
                    "content": "max body",
                    "panel_size": "maximized",
                    "content_format": "markdown",
                }
            ),
            content_type="application/json",
        )
        self.assertTrue(r.get_json()["ok"])
        again = self.client.put(
            "/api/notebook/notepad?scope=work",
            data=json.dumps(
                {
                    "scope": "work",
                    "content": "max body updated",
                    "panel_size": "maximized",
                }
            ),
            content_type="application/json",
        )
        pad = again.get_json()["notepad"]
        self.assertEqual(pad["content"], "max body updated")
        self.assertEqual(pad["panel_size"], "maximized")

    def test_ui_wires_minimize_and_full_viewport_maximize(self) -> None:
        page = self.client.get("/personal")
        html = page.get_data(as_text=True)
        self.assertIn('id="qn-expand"', html)
        self.assertIn('id="qn-maximize"', html)
        self.assertIn('id="qn-minimize"', html)
        self.assertIn('id="qn-close"', html)
        self.assertNotIn('id="qn-restore"', html)
        self.assertIn('id="qn-minimize"', html)
        # Revision Restore stays a class (not the drawer control id); rendered by JS/template.
        panel = (
            Path(__file__).resolve().parents[1]
            / "templates"
            / "partials"
            / "quick_notepad_panel.html"
        ).read_text(encoding="utf-8")
        self.assertIn('class="btn btn-sm qn-restore"', panel)
        self.assertIn(">Restore</button>", panel)
        self.assertIn('id="qn-minimize"', panel)
        self.assertIn(">Minimize</button>", panel)
        self.assertIn('aria-label="Minimize Quick Notepad"', html)
        self.assertIn("Minimize</button>", html)
        self.assertIn('id="qn-chrome"', html)
        self.assertIn("data-qn-size=", html)
        self.assertIn('aria-controls="qn-panel"', html)
        self.assertIn("aria-expanded=", html)
        self.assertIn('id="qn-copy"', html)
        self.assertIn("Revision history", html)
        self.assertIn('id="qn-mode-preview"', html)

        css = (
            Path(__file__).resolve().parents[1] / "static" / "css" / "style.css"
        ).read_text(encoding="utf-8")
        self.assertIn(".qn-chrome", css)
        self.assertIn("position: sticky", css)
        self.assertIn("inset: 0", css)
        self.assertIn("html.qn-maximized", css)
        self.assertIn("overflow: hidden !important", css)
        self.assertIn("z-index: 200", css)
        self.assertIn("100vw", css)
        self.assertIn("100vh", css)

        js = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "quick_notepad.js"
        ).read_text(encoding="utf-8")
        self.assertIn("resolveEscapeAction", js)
        self.assertIn('return "minimize"', js)
        self.assertIn("SIZE_MAXIMIZED", js)
        self.assertIn("minimizeSize", js)
        self.assertIn("setSizeMode", js)
        self.assertIn("captureCaret", js)
        self.assertIn("restoreCaret", js)
        self.assertIn("qn-maximized", js)
        self.assertIn("panel_size", js)
        self.assertNotIn("previousSizeMode", js)
        self.assertNotIn("function restoreSize", js)
        self.assertIn('.closest(".qn-restore")', js)


class PanelSizeJsHelperTests(unittest.TestCase):
    def test_escape_and_minimize_helpers(self) -> None:
        script = Path(__file__).resolve().parents[1] / "tests" / "quick_notepad_size.test.js"
        self.assertTrue(script.is_file())
        completed = subprocess.run(
            ["node", str(script)],
            cwd=str(Path(__file__).resolve().parents[1]),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertIn("OK", completed.stdout)


if __name__ == "__main__":
    unittest.main()
