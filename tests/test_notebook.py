"""Repository Notebook store, markdown, and route smoke tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from hub.notebook.db import NotebookDatabase
from hub.notebook.markdown_util import render_markdown
from hub.notebook.store import NotebookStore


class MarkdownTests(unittest.TestCase):
    def test_escapes_html_and_renders_bold_link(self) -> None:
        html = render_markdown('Hello **world** and [docs](https://example.com)\n\n<script>x</script>')
        self.assertIn("<strong>world</strong>", html)
        self.assertIn('href="https://example.com"', html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)

    def test_strikethrough_and_script_sanitization(self) -> None:
        html = render_markdown("Gone ~~old~~ keep <script>alert(1)</script>")
        self.assertIn("<del>old</del>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<script>", html)


class NotebookStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NotebookStore(NotebookDatabase(Path(self.tmp.name) / "notebook.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migrations_applied(self) -> None:
        applied = self.store.db.applied_migrations()
        self.assertIn("001_initial_notebook", applied)
        self.assertIn("002_note_pinned", applied)
        self.assertIn("004_note_scope_workspace", applied)

    def test_create_save_archive_restore_export(self) -> None:
        note = self.store.create(
            title="Wire enrichment",
            repository_id="live-processing",
            repository_label="Live Processing",
        )
        self.assertEqual(note["status"], "inbox")
        self.assertEqual(note["repositories"][0]["repository_id"], "live-processing")

        saved = self.store.save(
            note["id"],
            title="Wire enrichment",
            body_md="## Next\n- [ ] confirm gates",
            note_type="task",
            status="ongoing",
            priority="high",
            due_date="2026-08-01",
            tags="dhis2, hub",
            repositories=[
                {
                    "repository_id": "live-processing",
                    "repository_label": "Live Processing",
                    "role": "primary",
                },
                {
                    "repository_id": "gone-repo",
                    "repository_label": "Removed Repo",
                    "role": "related",
                },
            ],
            checklist=[{"text": "Write tests", "done": True}, {"text": "Docs", "done": False}],
            links=[{"label": "Handoff", "url": "https://example.com/handoff"}],
        )
        assert saved is not None
        self.assertEqual(saved["status"], "ongoing")
        self.assertEqual(saved["priority"], "high")
        self.assertEqual(len(saved["repositories"]), 2)
        self.assertEqual(saved["repositories"][1]["repository_label"], "Removed Repo")
        self.assertTrue(saved["checklist"][0]["done"])
        self.assertEqual(saved["links"][0]["label"], "Handoff")
        self.assertFalse(saved.get("pinned"))

        pinned = self.store.save(
            note["id"],
            title="Wire enrichment",
            body_md="## Next\n- [ ] confirm gates",
            note_type="task",
            status="ongoing",
            priority="high",
            due_date="2026-08-01",
            tags="dhis2, hub",
            repositories=saved["repositories"],
            checklist=saved["checklist"],
            links=saved["links"],
            pinned=True,
        )
        assert pinned is not None
        self.assertTrue(pinned["pinned"])
        self.assertEqual(pinned["checklist_progress"], "1/2")

        # Keep notes when a repository becomes unavailable (label preserved).
        listed = self.store.search(repository_id="gone-repo")
        self.assertEqual(len(listed), 1)
        self.assertIn("Removed Repo", listed[0]["repository_labels"])

        archived = self.store.archive(note["id"])
        assert archived is not None
        self.assertEqual(archived["status"], "archived")
        restored = self.store.restore(note["id"])
        assert restored is not None
        self.assertEqual(restored["status"], "inbox")

        payload = self.store.export_payload(note["id"])
        assert payload is not None
        self.assertEqual(payload["format"], "central-hub-notebook-v1")
        self.assertEqual(payload["note"]["id"], note["id"])

    def test_status_filter_and_search(self) -> None:
        a = self.store.create(title="Alpha blocked")
        self.store.save(
            a["id"],
            title="Alpha blocked",
            body_md="findme-token",
            note_type="bug",
            status="blocked",
            priority="urgent",
            due_date=None,
            tags="alpha",
            repositories=[],
            checklist=[],
            links=[],
        )
        self.store.create(title="Other")
        blocked = self.store.search(status="blocked")
        self.assertEqual(len(blocked), 1)
        found = self.store.search(q="findme-token")
        self.assertEqual(len(found), 1)
        tagged = self.store.search(tag="alpha")
        self.assertEqual(len(tagged), 1)

        done = self.store.create(title="Done note")
        self.store.save(
            done["id"],
            title="Done note",
            body_md="",
            note_type="task",
            status="done",
            priority="low",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
        )
        open_notes = self.store.search(status="open")
        titles = {n["title"] for n in open_notes}
        self.assertIn("Alpha blocked", titles)
        self.assertIn("Other", titles)
        self.assertNotIn("Done note", titles)
        self.assertEqual(self.store.status_counts()["open"], 2)


class NotebookRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import os
        from pathlib import Path

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        # Keep DHIS2 off for smoke
        for key in ("DHIS2_BASE_URL", "DHIS2_USERNAME", "DHIS2_PASSWORD"):
            os.environ.pop(key, None)

        import importlib
        import hub.settings as settings_mod
        import app as app_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        # Point notebook store at temp DB
        from hub.notebook.db import NotebookDatabase
        from hub.notebook.store import NotebookStore

        cls.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(root / "notebook.db"))
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_routes_smoke(self) -> None:
        r = self.client.get("/work/notebook")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Work Notebook", r.data)
        self.assertIn(b"All Notes", r.data)
        self.assertIn(b"Inbox", r.data)

        r_new = self.client.post("/work/notebook", data={"action": "new"}, follow_redirects=True)
        self.assertEqual(r_new.status_code, 200)
        self.assertIn(b"Untitled note", r_new.data)

        # Extract note id from export link or form
        html = r_new.get_data(as_text=True)
        self.assertIn('name="note_id"', html)
        start = html.find('name="note_id" value="') + len('name="note_id" value="')
        note_id = html[start : start + 32]

        r_save = self.client.post(
            "/work/notebook",
            data={
                "action": "save",
                "note_id": note_id,
                "title": "Smoke note",
                "body_md": "**bold**",
                "note_type": "task",
                "status": "pending",
                "priority": "medium",
                "due_date": "",
                "tags": "smoke",
                "repo_id": "sample-cli",
                "repo_label": "Sample CLI",
                "repo_role": "primary",
                "check_text": "One",
                "check_done_flag": "1",
                "link_label": "Site",
                "link_url": "https://example.com",
            },
            follow_redirects=True,
        )
        self.assertEqual(r_save.status_code, 200)
        self.assertIn(b"Smoke note", r_save.data)
        self.assertIn(b"Note saved", r_save.data)

        r_prev = self.client.post(
            "/api/notebook/preview",
            data=json.dumps({"markdown": "## Hi"}),
            content_type="application/json",
        )
        self.assertEqual(r_prev.status_code, 200)
        self.assertIn("<h2>", r_prev.get_json()["html"])

        r_export = self.client.get(f"/notebook/{note_id}/export")
        self.assertEqual(r_export.status_code, 200)
        payload = json.loads(r_export.data)
        self.assertEqual(payload["note"]["title"], "Smoke note")

        r_arch = self.client.post(
            "/work/notebook",
            data={"action": "archive", "note_id": note_id},
            follow_redirects=True,
        )
        self.assertEqual(r_arch.status_code, 200)
        self.assertIn(b"archived", r_arch.data.lower())

        r_rest = self.client.post(
            "/work/notebook",
            data={"action": "restore", "note_id": note_id},
            follow_redirects=True,
        )
        self.assertEqual(r_rest.status_code, 200)
        self.assertIn(b"restored", r_rest.data.lower())

    def test_work_notebook_filters(self) -> None:
        store: NotebookStore = self.app.config["NOTEBOOK"]
        task = store.create(title="Filter Task", scope="work", note_type="task")
        store.save(
            task["id"],
            title="Filter Task",
            body_md="needle-token",
            note_type="task",
            status="ongoing",
            priority="high",
            due_date=None,
            tags="filter-tag",
            repositories=[
                {
                    "repository_id": "sample-cli",
                    "repository_label": "Sample CLI",
                    "role": "primary",
                }
            ],
            checklist=[],
            links=[],
            scope="work",
        )
        note = store.create(title="Other Note", scope="work", note_type="note")
        store.save(
            note["id"],
            title="Other Note",
            body_md="zzz",
            note_type="note",
            status="inbox",
            priority="low",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            scope="work",
        )

        r_type = self.client.get("/work/notebook?status=all&type=task")
        self.assertEqual(r_type.status_code, 200)
        body = r_type.get_data(as_text=True)
        self.assertIn("Filter Task", body)
        self.assertNotIn("Other Note", body)
        self.assertIn('value="task" selected', body)
        self.assertIn(">Clear<", body)

        r_miss = self.client.get("/work/notebook?status=all&type=bug")
        miss = r_miss.get_data(as_text=True)
        self.assertNotIn("Filter Task", miss)
        self.assertIn("No notes match these filters", miss)

        # Open note that does not match filters should be dropped from selection.
        r_pin = self.client.get(
            f"/work/notebook?status=all&type=task&note={note['id']}"
        )
        pinned = r_pin.get_data(as_text=True)
        self.assertIn("Filter Task", pinned)
        self.assertNotIn('value="' + note["id"] + '"', pinned.split("nb-editor-form")[1][:400])


if __name__ == "__main__":
    unittest.main()
