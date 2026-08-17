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
        # VANTA primary surface is Code Workspace.
        self.assertTrue(resp.headers["Location"].endswith("/work/climate"))

    def test_switch_workspace_remembers_cookie_and_pref(self) -> None:
        resp = self.client.get("/workspace/personal", follow_redirects=False)
        self.assertEqual(resp.status_code, 302)
        # ARCTIC primary surface is the personal dashboard (Code Workspace is VANTA-only).
        self.assertTrue(resp.headers["Location"].endswith("/personal"))
        self.assertIn(COOKIE_NAME, resp.headers.get("Set-Cookie", ""))
        store: NotebookStore = self.app.config["NOTEBOOK"]
        self.assertEqual(get_pref(store.db, "workspace"), "personal")
        # Next / visit uses remembered workspace → personal dashboard.
        resp2 = self.client.get("/")
        self.assertEqual(resp2.status_code, 302)
        self.assertTrue(resp2.headers["Location"].endswith("/personal"))
        # VANTA switch lands on Code Workspace; Dashboard remains at /work.
        resp3 = self.client.get("/workspace/work", follow_redirects=False)
        self.assertEqual(resp3.status_code, 302)
        self.assertTrue(resp3.headers["Location"].endswith("/work/climate"))
        dash = self.client.get("/work")
        self.assertEqual(dash.status_code, 200)
        self.assertIn("Work Dashboard", dash.get_data(as_text=True))

    def test_switch_workspace_preserves_equivalent_section(self) -> None:
        # Dashboard ↔ Dashboard
        resp = self.client.get(
            "/workspace/personal?from_endpoint=work_dashboard",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal"))
        resp = self.client.get(
            "/workspace/work?from_endpoint=personal_dashboard",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/work"))
        # Notebook ↔ Notebook
        resp = self.client.get(
            "/workspace/personal?from_endpoint=work_notebook",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal/notebook"))
        # Chat ↔ Chat
        resp = self.client.get(
            "/workspace/personal?from_endpoint=work_climate_chat",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal/chat"))
        # VANTA-only Repositories leave the page and land on the ARCTIC dashboard.
        resp = self.client.get(
            "/workspace/personal?from_endpoint=repositories",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal"))
        # VANTA-only Code Workspace also lands on the ARCTIC dashboard.
        resp = self.client.get(
            "/workspace/personal?from_endpoint=work_climate",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal"))
        # Tasks ↔ Tasks
        resp = self.client.get(
            "/workspace/personal?from_endpoint=work_tasks",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal/tasks"))
        # VANTA-only SQL falls back to the ARCTIC dashboard.
        resp = self.client.get(
            "/workspace/personal?from_endpoint=sql_workspace",
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers["Location"].endswith("/personal"))

    def test_nav_sections_personal_vs_work(self) -> None:
        work_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("workspace-switcher", work_html)
        self.assertIn('class="climate-system-shell theme-work"', work_html)
        self.assertIn(">VANTA<", work_html)
        self.assertLess(work_html.index(">VANTA<"), work_html.index(">ARCTIC<"))
        self.assertIn("Code Workspace", work_html)
        self.assertIn("CLIMATE Chat", work_html)
        self.assertIn("Repositories", work_html)
        self.assertIn("Notebook", work_html)
        self.assertIn(">Tasks<", work_html)
        self.assertIn("SQL Workspace", work_html)
        self.assertIn(">DHIS2<", work_html)
        self.assertNotIn("CLIMATE · VANTA", work_html)
        self.assertIn("sidebar-collapse-btn", work_html)
        self.assertIn(">System<", work_html)
        self.assertIn("Audit", work_html)
        self.assertIn("Settings", work_html)
        self.assertNotIn("Personal Notebook", work_html)
        # VANTA CLIMATE order: Dashboard, Chat, Code Workspace, Tasks, Notebook, Repositories.
        w_side = work_html[
            work_html.find('class="sidebar-nav"') : work_html.find('class="sidebar-actions"')
        ]
        self.assertLess(w_side.index(">Dashboard<"), w_side.index("CLIMATE Chat"))
        self.assertLess(w_side.index("CLIMATE Chat"), w_side.index("Code Workspace"))
        self.assertLess(w_side.index("Code Workspace"), w_side.index(">Tasks<"))
        self.assertLess(w_side.index(">Tasks<"), w_side.index("Notebook"))
        self.assertLess(w_side.index("Notebook"), w_side.index("Repositories"))
        self.assertIn("SQL Workspace", w_side)
        self.assertIn("/sql", w_side)
        self.assertNotIn("Personal Files", w_side)
        self.assertNotIn(">Aira<", w_side)
        # Quick Notepad opens from the activity rail (no floating pill).
        self.assertIn("id=\"qn-panel\"", work_html)
        self.assertIn('id="ar-notepad"', work_html)
        self.assertIn('class="qn-open-btn sr-only"', work_html)
        self.assertNotIn('href="/work#quick-notepad"', work_html)

        personal_html = self.client.get("/personal").get_data(as_text=True)
        self.assertIn('class="climate-system-shell theme-personal"', personal_html)
        self.assertIn(">ARCTIC<", personal_html)
        self.assertIn("CLIMATE Chat", personal_html)
        self.assertIn("Notebook", personal_html)
        self.assertIn("Tasks", personal_html)
        self.assertIn("Settings", personal_html)
        p_side = personal_html[
            personal_html.find('class="sidebar-nav"') : personal_html.find('class="sidebar-actions"')
        ]
        self.assertLess(p_side.index(">Dashboard<"), p_side.index("CLIMATE Chat"))
        self.assertLess(p_side.index("CLIMATE Chat"), p_side.index(">Tasks<"))
        self.assertLess(p_side.index(">Tasks<"), p_side.index("Notebook"))
        self.assertLess(p_side.index("Notebook"), p_side.index("Settings"))
        self.assertNotIn("Code Workspace", p_side)
        self.assertNotIn(">Repositories<", p_side)
        self.assertNotIn('href="/repositories"', p_side)
        self.assertNotIn('href="/work/climate"', p_side)
        self.assertNotIn('href="/personal/climate"', p_side)
        self.assertLess(p_side.index(">Dashboard<"), p_side.index("Personal Files"))
        self.assertNotIn("HCSC–RF", p_side)
        self.assertNotIn("/dhis2/hcsc-indicators", p_side)
        self.assertNotIn("SQL Workspace", p_side)
        self.assertNotIn('href="/sql"', p_side)
        self.assertNotIn(">DHIS2<", p_side)
        self.assertIn("id=\"qn-panel\"", personal_html)
        self.assertIn('id="ar-notepad"', personal_html)
        self.assertNotIn('href="/personal#quick-notepad"', personal_html)
        self.assertNotIn("DHIS2 Reports", personal_html)
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
            "/work/tasks",
            "/repositories",
            "/sql",
            "/work/airix",
            "/work/email",
            "/work/calendar",
        ):
            html = self.client.get(path, follow_redirects=True).get_data(as_text=True)
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

    def test_work_tasks_page_is_scoped(self) -> None:
        store: NotebookStore = self.app.config["NOTEBOOK"]
        store.create(title="Personal only task", scope="personal")
        work = store.create(title="Work only task", scope="work")
        store.save(
            work["id"],
            title="Work only task",
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
        html = self.client.get("/work/tasks").get_data(as_text=True)
        self.assertIn("Work only task", html)
        self.assertNotIn("Personal only task", html)
        personal_html = self.client.get("/personal/tasks").get_data(as_text=True)
        self.assertIn("Personal only task", personal_html)
        self.assertNotIn("Work only task", personal_html)

    def test_personal_dashboard_hides_vanta_repos(self) -> None:
        html = self.client.get("/personal").get_data(as_text=True)
        self.assertNotIn("Connected Repositories", html)
        self.assertEqual(html.count("Recent Activity"), 1)
        self.assertIn("Personal Tasks", html)
        self.assertNotIn("SQL Workspace", html.split("sidebar-nav")[1].split("sidebar-actions")[0])
        self.assertNotIn("DHIS2 Maintenance", html)

    def test_vanta_routes_remain_available_without_arctic_nav(self) -> None:
        climate = self.client.get("/work/climate")
        self.assertEqual(climate.status_code, 200)
        self.assertIn('id="climate-monaco"', climate.get_data(as_text=True))
        personal_climate = self.client.get("/personal/climate")
        self.assertEqual(personal_climate.status_code, 200)
        personal_side = personal_climate.get_data(as_text=True)
        p_side = personal_side[
            personal_side.find('class="sidebar-nav"') : personal_side.find('class="sidebar-actions"')
        ]
        self.assertNotIn("Code Workspace", p_side)
        repos = self.client.get("/repositories")
        self.assertEqual(repos.status_code, 200)
        repo_html = repos.get_data(as_text=True)
        r_side = repo_html[
            repo_html.find('class="sidebar-nav"') : repo_html.find('class="sidebar-actions"')
        ]
        self.assertIn("Code Workspace", r_side)
        self.assertIn("Repositories", r_side)

    def test_shared_settings_and_chat_keep_workspace_shell(self) -> None:
        work_chat = self.client.get("/work/chat").get_data(as_text=True)
        personal_chat = self.client.get("/personal/chat").get_data(as_text=True)
        settings = self.client.get("/settings").get_data(as_text=True)
        for html in (work_chat, personal_chat, settings):
            self.assertIn("workspace-switcher", html)
            self.assertIn(">VANTA<", html)
            self.assertIn(">ARCTIC<", html)
            self.assertIn("CLIMATE Chat", html)
            self.assertIn("Settings", html)
        work_side = work_chat[
            work_chat.find('class="sidebar-nav"') : work_chat.find('class="sidebar-actions"')
        ]
        personal_side = personal_chat[
            personal_chat.find('class="sidebar-nav"') : personal_chat.find('class="sidebar-actions"')
        ]
        self.assertIn("SQL Workspace", work_side)
        self.assertIn("Code Workspace", work_side)
        self.assertIn("Repositories", work_side)
        self.assertNotIn("SQL Workspace", personal_side)
        self.assertNotIn("Code Workspace", personal_side)
        self.assertNotIn(">Repositories<", personal_side)
        self.assertIn("Personal Files", personal_side)
        self.assertNotIn("Personal Files", work_side)


if __name__ == "__main__":
    unittest.main()
