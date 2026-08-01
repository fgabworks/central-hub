"""Personal / Work workspace navigation, scope migration, and redirects."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app
from hub.notebook.db import NotebookDatabase
from hub.notebook.store import NotebookStore
from hub.notebook.workspace import (
    COOKIE_NAME,
    get_pref,
    persist_workspace,
)


class ScopeMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "notebook.db"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migration_004_sets_existing_notes_to_work(self) -> None:
        db = NotebookDatabase(self.db_path)
        self.assertIn("004_note_scope_workspace", db.applied_migrations())
        store = NotebookStore(db)
        note = store.create(title="Legacy linked", repository_id="sample-cli")
        self.assertEqual(note["scope"], "work")
        # Backfill path: insert without going through create() semantics.
        with db.connect() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    id, title, body_md, note_type, status, priority, due_date,
                    tags_json, created_at, updated_at, archived_at, pinned, scope
                ) VALUES ('legacy1', 'Old', '', 'task', 'pending', 'medium', NULL,
                          '[]', datetime('now'), datetime('now'), NULL, 0, 'work')
                """
            )
        got = store.get("legacy1")
        self.assertEqual(got["scope"], "work")

    def test_personal_note_has_no_repositories(self) -> None:
        store = NotebookStore(NotebookDatabase(self.db_path))
        note = store.create(
            title="Personal idea",
            scope="personal",
            repository_id="should-ignore",
        )
        self.assertEqual(note["scope"], "personal")
        self.assertEqual(note["repositories"], [])
        saved = store.save(
            note["id"],
            title="Personal idea",
            body_md="hello",
            note_type="task",
            status="pending",
            priority="medium",
            due_date=None,
            tags=[],
            repositories=[
                {
                    "repository_id": "sample-cli",
                    "repository_label": "Sample",
                    "role": "primary",
                }
            ],
            checklist=[],
            links=[],
            scope="personal",
        )
        self.assertEqual(saved["repositories"], [])

    def test_workspace_pref_persists(self) -> None:
        db = NotebookDatabase(self.db_path)
        self.assertEqual(get_pref(db, "workspace", "work"), "work")
        persist_workspace(db, "personal")
        self.assertEqual(get_pref(db, "workspace"), "personal")


class WorkspaceNavRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "notebook.db"
        self.app = create_app()
        self.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(self.db_path))
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_root_redirects_to_work_by_default(self) -> None:
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/work"))

    def test_switch_workspace_remembers_cookie_and_pref(self) -> None:
        resp = self.client.get("/workspace/personal", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal"))
        self.assertIn(COOKIE_NAME, resp.headers.get("Set-Cookie", ""))
        store: NotebookStore = self.app.config["NOTEBOOK"]
        self.assertEqual(get_pref(store.db, "workspace"), "personal")
        # Next / visit uses remembered workspace.
        resp2 = self.client.get("/")
        self.assertEqual(resp2.status_code, 302)
        self.assertTrue(resp2.headers["Location"].endswith("/personal"))

    def test_nav_sections_personal_vs_work(self) -> None:
        work_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("workspace-switcher", work_html)
        self.assertIn('class="theme-work"', work_html)
        self.assertIn("Work Dashboard", work_html)
        self.assertIn("Repositories", work_html)
        self.assertIn("Work Notebook", work_html)
        self.assertIn("SQL Workspace", work_html)
        self.assertIn("DHIS2 Reports", work_html)
        self.assertIn("HCSC–RF", work_html)
        self.assertIn("/dhis2/hcsc-indicators", work_html)
        self.assertLess(work_html.index("DHIS2 Reports"), work_html.index("HCSC–RF"))
        self.assertIn(">System<", work_html)
        self.assertIn("Audit", work_html)
        self.assertNotIn("Personal Notebook", work_html)
        # Quick Notepad opens from the activity rail (no floating pill).
        self.assertIn("id=\"qn-panel\"", work_html)
        self.assertIn('id="ar-notepad"', work_html)
        self.assertIn('class="qn-open-btn sr-only"', work_html)
        self.assertNotIn('href="/work#quick-notepad"', work_html)

        personal_html = self.client.get("/personal").get_data(as_text=True)
        self.assertIn('class="theme-personal"', personal_html)
        self.assertIn("Personal Dashboard", personal_html)
        self.assertIn("Personal Notebook", personal_html)
        self.assertIn("Personal Tasks", personal_html)
        p_side = personal_html[
            personal_html.find('class="sidebar-nav"') : personal_html.find('class="sidebar-actions"')
        ]
        self.assertNotIn("HCSC–RF", p_side)
        self.assertNotIn("/dhis2/hcsc-indicators", p_side)
        self.assertIn("id=\"qn-panel\"", personal_html)
        self.assertIn('id="ar-notepad"', personal_html)
        self.assertNotIn('href="/personal#quick-notepad"', personal_html)
        self.assertNotIn("Connected Repositories", personal_html)
        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn("body.theme-personal", css)
        self.assertIn("#0D5561", css)
        self.assertIn("--accent-metallic", css)

    def test_floating_notepad_on_main_pages_without_sidebar_entry(self) -> None:
        """Main pages expose rail notepad; sidebar no longer lists Quick Notepad."""
        for path in (
            "/personal",
            "/personal/notebook",
            "/personal/tasks",
            "/personal/email",
            "/personal/calendar",
            "/work",
            "/work/notebook",
            "/repositories",
            "/sql",
            "/agents",
            "/work/email",
            "/work/calendar",
        ):
            html = self.client.get(path).get_data(as_text=True)
            self.assertIn('id="ar-notepad"', html, msg=path)
            self.assertIn('id="qn-panel"', html, msg=path)
            self.assertIn("qn-open-btn sr-only", html, msg=path)
            self.assertNotIn(">Quick Notepad</a>", html, msg=path)
            self.assertNotIn('href="/work#quick-notepad"', html, msg=path)
            self.assertNotIn('href="/personal#quick-notepad"', html, msg=path)
            self.assertIn('id="qn-panel"', html, msg=path)
            self.assertNotIn("#quick-notepad", html.split("sidebar-nav")[1].split("sidebar-actions")[0], msg=path)

    def test_legacy_notebook_redirects_by_note_scope(self) -> None:
        store: NotebookStore = self.app.config["NOTEBOOK"]
        work = store.create(title="Work note", scope="work")
        personal = store.create(title="Personal note", scope="personal")
        r1 = self.client.get(f"/notebook?note={work['id']}")
        self.assertEqual(r1.status_code, 302)
        self.assertIn("/work/notebook", r1.headers["Location"])
        r2 = self.client.get(f"/notebook?note={personal['id']}")
        self.assertEqual(r2.status_code, 302)
        self.assertIn("/personal/notebook", r2.headers["Location"])

    def test_work_queue_excludes_personal_notes(self) -> None:
        store: NotebookStore = self.app.config["NOTEBOOK"]
        store.create(title="Only personal", scope="personal")
        work = store.create(title="Only work", scope="work")
        store.save(
            work["id"],
            title="Only work",
            body_md="",
            note_type="task",
            status="pending",
            priority="medium",
            due_date=None,
            tags=[],
            repositories=[],
            checklist=[],
            links=[],
            scope="work",
        )
        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("Only work", html)
        self.assertNotIn("Only personal", html)

    def test_personal_tasks_page_loads(self) -> None:
        resp = self.client.get("/personal/tasks")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Personal Tasks", html)
        self.assertIn("id=\"qn-panel\"", html)


if __name__ == "__main__":
    unittest.main()
