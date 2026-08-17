"""Dashboard Notebook Work Queue helpers and route smoke tests."""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from hub.notebook.dashboard import (
    DASHBOARD_QUEUE_FETCH_LIMIT,
    DASHBOARD_QUEUE_VISIBLE_ROWS,
    build_repo_summary,
    classify_open_note,
    dashboard_work_queue,
    filter_queue,
    open_task_stats,
    open_tasks_severity,
)
from hub.notebook.db import NotebookDatabase
from hub.notebook.store import NotebookStore


class WorkQueueHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.today = date(2026, 7, 25)
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NotebookStore(NotebookDatabase(Path(self.tmp.name) / "notebook.db"))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self) -> None:
        specs = [
            ("Pinned overdue", "ongoing", "2026-07-20", True, True),
            ("Due today", "pending", "2026-07-25", False, False),
            ("Upcoming week", "inbox", "2026-07-28", False, False),
            ("Blocked item", "blocked", "2026-08-01", False, True),
            ("Done excluded", "done", "2026-07-20", True, False),
            ("Archived excluded", "archived", "2026-07-20", True, False),
        ]
        for title, status, due, pinned, with_checks in specs:
            note = self.store.create(title=title)
            checklist = (
                [{"text": "A", "done": True}, {"text": "B", "done": False}, {"text": "C", "done": False}]
                if with_checks
                else []
            )
            self.store.save(
                note["id"],
                title=title,
                body_md="",
                note_type="task",
                status=status,
                priority="high",
                due_date=due,
                tags="",
                repositories=[
                    {
                        "repository_id": "sample-cli",
                        "repository_label": "Sample CLI",
                        "role": "primary",
                    }
                ],
                checklist=checklist,
                links=[],
                pinned=pinned,
            )

    def test_migration_includes_pinned(self) -> None:
        applied = self.store.db.applied_migrations()
        self.assertIn("001_initial_notebook", applied)
        self.assertIn("002_note_pinned", applied)

    def test_list_open_excludes_done_and_archived(self) -> None:
        self._seed()
        open_notes = self.store.list_open()
        titles = {n["title"] for n in open_notes}
        self.assertEqual(
            titles,
            {"Pinned overdue", "Due today", "Upcoming week", "Blocked item"},
        )
        pinned = next(n for n in open_notes if n["title"] == "Pinned overdue")
        self.assertTrue(pinned["pinned"])
        self.assertEqual(pinned["checklist_progress"], "1/3")

    def test_open_task_stats_and_tabs(self) -> None:
        self._seed()
        notes = self.store.list_open()
        stats = open_task_stats(notes, today=self.today)
        self.assertEqual(stats["open"], 4)
        self.assertEqual(stats["overdue"], 1)
        self.assertEqual(stats["due_today"], 1)
        self.assertEqual(stats["upcoming"], 2)
        self.assertEqual(stats["blocked"], 1)
        self.assertEqual(stats["pinned"], 1)
        # due_this_week: today <= due <= today+7 → Due today, Jul 28, Aug 1 blocked
        self.assertEqual(stats["due_this_week"], 3)

        queue = dashboard_work_queue(self.store, tab="open", limit=5, today=self.today)
        self.assertEqual(queue["tabs"]["open"], 4)
        self.assertEqual(len(queue["notes"]), 4)
        titles = {n["title"] for n in queue["notes"]}
        self.assertIn("Pinned overdue", titles)
        self.assertIn("Due today", titles)

        pinned_q = dashboard_work_queue(self.store, tab="pinned", limit=5, today=self.today)
        self.assertEqual(pinned_q["tabs"]["pinned"], 1)
        self.assertEqual(len(pinned_q["notes"]), 1)
        self.assertEqual(pinned_q["notes"][0]["title"], "Pinned overdue")
        self.assertEqual(pinned_q["notes"][0]["due_meta"]["kind"], "overdue")

        overdue = filter_queue(notes, "overdue", today=self.today)
        self.assertEqual([n["title"] for n in overdue], ["Pinned overdue"])

        due_today = filter_queue(notes, "due_today", today=self.today)
        self.assertEqual([n["title"] for n in due_today], ["Due today"])

        blocked = filter_queue(notes, "blocked", today=self.today)
        self.assertEqual([n["title"] for n in blocked], ["Blocked item"])

        # Open tab: Pending first among statuses (Done last when present).
        open_sorted = filter_queue(notes, "open", today=self.today, limit=10)
        self.assertEqual(open_sorted[0]["status"], "pending")
        self.assertEqual(open_sorted[0]["title"], "Due today")
        status_order = [n["status"] for n in open_sorted]
        self.assertEqual(status_order, ["pending", "inbox", "ongoing", "blocked"])

    def test_queue_pending_first_done_last(self) -> None:
        notes = [
            {
                "title": "Done item",
                "status": "done",
                "due_date": "2026-07-20",
                "pinned": True,
                "priority": "high",
                "note_type": "task",
                "updated_at": "2026-07-25T12:00:00",
            },
            {
                "title": "Ongoing item",
                "status": "ongoing",
                "due_date": "2026-07-28",
                "pinned": False,
                "priority": "medium",
                "note_type": "task",
                "updated_at": "2026-07-25T11:00:00",
            },
            {
                "title": "Pending item",
                "status": "pending",
                "due_date": None,
                "pinned": False,
                "priority": "low",
                "note_type": "note",
                "updated_at": "2026-07-25T10:00:00",
            },
            {
                "title": "Blocked item",
                "status": "blocked",
                "due_date": "2026-07-26",
                "pinned": False,
                "priority": "urgent",
                "note_type": "bug",
                "updated_at": "2026-07-25T13:00:00",
            },
        ]
        ordered = filter_queue(notes, "open", today=self.today, limit=10)
        self.assertEqual(
            [n["title"] for n in ordered],
            ["Pending item", "Ongoing item", "Blocked item", "Done item"],
        )

    def test_classify_due_labels(self) -> None:
        item = classify_open_note(
            {"due_date": "2026-07-25", "status": "pending", "pinned": False, "priority": "medium", "note_type": "task"},
            today=self.today,
        )
        self.assertEqual(item["due_meta"]["label"], "Today")
        overdue = classify_open_note(
            {"due_date": "2026-07-01", "status": "ongoing", "pinned": True, "priority": "high", "note_type": "bug"},
            today=self.today,
        )
        self.assertTrue(overdue["flags"]["overdue"])
        self.assertEqual(overdue["due_meta"]["label"], "Overdue")

    def test_open_tasks_severity_levels(self) -> None:
        self.assertEqual(open_tasks_severity({"open": 0, "overdue": 0, "blocked": 0, "urgent": 0}), "neutral")
        self.assertEqual(
            open_tasks_severity({"open": 3, "overdue": 0, "blocked": 0, "due_this_week": 1, "urgent": 0}),
            "neutral",
        )
        self.assertEqual(
            open_tasks_severity({"open": 2, "overdue": 1, "blocked": 0, "urgent": 0}),
            "alert",
        )
        self.assertEqual(
            open_tasks_severity({"open": 1, "overdue": 0, "blocked": 1, "urgent": 2}),
            "alert",
        )
        self.assertEqual(
            open_tasks_severity({"open": 4, "overdue": 0, "blocked": 0, "urgent": 1}),
            "alert",
        )

    def test_progress_with_and_without_checklist(self) -> None:
        with_items = self.store.create(title="Has checklist")
        self.store.save(
            with_items["id"],
            title="Has checklist",
            body_md="",
            note_type="task",
            status="ongoing",
            priority="medium",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[{"text": "A", "done": True}, {"text": "B", "done": False}],
            links=[],
            pinned=False,
        )
        without = self.store.create(title="No checklist")
        self.store.save(
            without["id"],
            title="No checklist",
            body_md="",
            note_type="note",
            status="inbox",
            priority="low",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )

        queue = dashboard_work_queue(self.store, tab="open", limit=10, today=self.today)
        by_title = {n["title"]: n for n in queue["notes"]}
        self.assertEqual(by_title["Has checklist"]["checklist_total"], 2)
        self.assertEqual(by_title["Has checklist"]["checklist_done"], 1)
        self.assertEqual(by_title["Has checklist"]["checklist_progress"], "1/2")
        self.assertEqual(by_title["No checklist"]["checklist_total"], 0)
        self.assertEqual(by_title["No checklist"]["checklist_done"], 0)

    def test_repo_summary_primary_plus_n_and_unavailable(self) -> None:
        note = {
            "repositories": [
                {
                    "repository_id": "gone-repo",
                    "repository_label": "Removed Repo",
                    "role": "related",
                    "sort_order": 1,
                },
                {
                    "repository_id": "live-processing",
                    "repository_label": "PMNP Live Processing",
                    "role": "primary",
                    "sort_order": 0,
                },
                {
                    "repository_id": "data-script",
                    "repository_label": "Data-Script",
                    "role": "depends-on",
                    "sort_order": 2,
                },
            ]
        }
        summary = build_repo_summary(note, registered_ids={"live-processing", "data-script"})
        self.assertEqual(summary["repo_primary_name"], "PMNP Live Processing")
        self.assertEqual(summary["repo_extra_count"], 2)
        self.assertEqual(summary["repo_cell"], "PMNP Live Processing +2")
        self.assertFalse(summary["repo_primary_unavailable"])
        self.assertIn("Primary: PMNP Live Processing", summary["repo_tooltip"])
        self.assertIn("Related: Removed Repo (Unavailable)", summary["repo_tooltip"])
        self.assertIn("Depends on: Data-Script", summary["repo_tooltip"])

        missing = build_repo_summary(
            {
                "repositories": [
                    {
                        "repository_id": "gone-repo",
                        "repository_label": "Removed Repo",
                        "role": "primary",
                        "sort_order": 0,
                    }
                ]
            },
            registered_ids={"live-processing"},
        )
        self.assertEqual(missing["repo_cell"], "Removed Repo (Unavailable)")
        self.assertTrue(missing["repo_primary_unavailable"])

        # Persisted associations still drive the queue when registry ids are passed through.
        self._seed()
        queue = dashboard_work_queue(
            self.store,
            tab="open",
            limit=5,
            today=self.today,
            registered_ids={"sample-cli"},
        )
        for item in queue["notes"]:
            self.assertEqual(item["repo_cell"], "Sample CLI")
            self.assertIn("Primary: Sample CLI", item["repo_tooltip"])


class DashboardRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import os
        import importlib

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)
        os.environ["CENTRAL_HUB_AUDIT_LOG"] = str(root / "audit.jsonl")
        os.environ["CENTRAL_HUB_DATABASE"] = str(root / "hub.db")
        for key in ("DHIS2_BASE_URL", "DHIS2_USERNAME", "DHIS2_PASSWORD"):
            os.environ.pop(key, None)

        import hub.settings as settings_mod
        import app as app_mod

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        cls.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(root / "notebook.db"))
        cls.client = cls.app.test_client()
        cls.store = cls.app.config["NOTEBOOK"]

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_dashboard_shows_work_queue_not_recent_jobs(self) -> None:
        note = self.store.create(title="Dash queue note")
        self.store.save(
            note["id"],
            title="Dash queue note",
            body_md="",
            note_type="task",
            status="ongoing",
            priority="high",
            due_date="2026-07-20",
            tags="",
            repositories=[
                {
                    "repository_id": "sample-cli",
                    "repository_label": "Sample CLI",
                    "role": "primary",
                }
            ],
            checklist=[{"text": "One", "done": True}, {"text": "Two", "done": False}],
            links=[],
            pinned=True,
        )

        r = self.client.get("/work")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Notebook Work Queue", html)
        self.assertIn("Open Tasks", html)
        self.assertIn("Dash queue note", html)
        self.assertIn("stat-card-tasks", html)
        self.assertIn("severity-", html)
        self.assertIn('href="/work/tasks"', html)
        self.assertIn("stat-card-link", html)
        self.assertIn("summary-compact", html)
        self.assertIn("urgent", html.lower())
        self.assertIn("overdue", html.lower())
        self.assertIn("Dash queue note", html)
        self.assertIn("Sample CLI", html)
        self.assertIn(">Repository<", html)
        self.assertIn('class="col-repo"', html)
        self.assertIn("1/2", html)
        self.assertIn("dash-repo-grid", html)
        self.assertIn("dash-grid-lower", html)
        self.assertIn("Connected Repositories", html)
        self.assertIn("Open (", html)
        # Empty checklist notes render an em dash, not 0/0, and omit the bar.
        empty = self.store.create(title="Empty progress note")
        self.store.save(
            empty["id"],
            title="Empty progress note",
            body_md="",
            note_type="note",
            status="inbox",
            priority="low",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=True,
        )
        html_empty = self.client.get("/work?queue=open").get_data(as_text=True)
        self.assertIn("Empty progress note", html_empty)
        # Progress cell for zero-total uses the empty label class (not a 0/0 bar).
        self.assertIn('check-progress-label is-empty', html_empty)
        self.assertNotRegex(
            html_empty,
            r"Empty progress note[\s\S]{0,400}?check-progress-bar",
        )
        self.assertIn("Pinned (", html)
        self.assertIn("Overdue (", html)
        self.assertIn("Due Today (", html)
        self.assertIn("Upcoming (", html)
        self.assertIn("Blocked (", html)
        self.assertIn("+ New Note", html)
        self.assertNotIn("Recent Jobs", html)
        self.assertNotIn("Active jobs", html)

        # Unpinned / undated open notes still appear on the default Open tab.
        undated = self.store.create(title="Undated open note")
        self.store.save(
            undated["id"],
            title="Undated open note",
            body_md="",
            note_type="note",
            status="inbox",
            priority="medium",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )
        r_open = self.client.get("/work?queue=open")
        self.assertIn("Undated open note", r_open.get_data(as_text=True))
        r_pinned = self.client.get("/work?queue=pinned")
        self.assertNotIn("Undated open note", r_pinned.get_data(as_text=True))

        # Jobs page still exists.
        jobs = self.client.get("/jobs")
        self.assertEqual(jobs.status_code, 200)
        self.assertIn(b"Jobs", jobs.data)

        # Done notes excluded from open tasks card value path.
        self.store.save(
            note["id"],
            title="Dash queue note",
            body_md="",
            note_type="task",
            status="done",
            priority="high",
            due_date="2026-07-20",
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=True,
        )
        r2 = self.client.get("/work?queue=open")
        html2 = r2.get_data(as_text=True)
        self.assertNotIn("Dash queue note", html2)
        self.assertIn("Undated open note", html2)

    def test_dashboard_queue_five_row_viewport(self) -> None:
        """Queue shows a 5-row viewport; all matching tasks remain in the scrollable body."""
        self.assertEqual(DASHBOARD_QUEUE_VISIBLE_ROWS, 5)
        self.assertGreater(DASHBOARD_QUEUE_FETCH_LIMIT, 5)

        # Empty work queue stays compact.
        empty_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("panel-queue is-empty", empty_html)
        self.assertIn("No open notebook items in this tab", empty_html)
        self.assertIn("Showing 0 of 0 open tasks", empty_html)

        def _make(title: str, *, scope: str = "work", status: str = "ongoing") -> None:
            note = self.store.create(title=title, scope=scope)
            self.store.save(
                note["id"],
                title=title,
                body_md="",
                note_type="task",
                status=status,
                priority="medium",
                due_date=None,
                tags="",
                repositories=[],
                checklist=[],
                links=[],
                pinned=False,
                scope=scope,
            )

        _make("Queue row 1")
        one_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("Queue row 1", one_html)
        self.assertIn("Showing 1 of 1 open tasks", one_html)
        self.assertNotIn("panel-queue is-empty", one_html)

        for i in range(2, 6):
            _make(f"Queue row {i}")
        five_html = self.client.get("/work").get_data(as_text=True)
        for i in range(1, 6):
            self.assertIn(f"Queue row {i}", five_html)
        self.assertIn("Showing 5 of 5 open tasks", five_html)

        for i in range(6, 9):
            _make(f"Queue row {i}")
        # Personal-scoped note must not leak into Work queue.
        _make("Personal only row", scope="personal")

        more_html = self.client.get("/work").get_data(as_text=True)
        for i in range(1, 9):
            self.assertIn(f"Queue row {i}", more_html)
        self.assertNotIn("Personal only row", more_html)
        self.assertIn("Showing 8 of 8 open tasks", more_html)
        self.assertIn('class="queue-scroll"', more_html)
        self.assertIn('class="queue-tabs"', more_html)
        # Header + tabs stay above the scroll region; footer stays below.
        header_idx = more_html.find("Notebook Work Queue")
        tabs_idx = more_html.find('class="queue-tabs"', header_idx)
        scroll_idx = more_html.find('class="queue-scroll"', header_idx)
        footer_idx = more_html.find("Showing 8 of 8 open tasks", header_idx)
        self.assertLess(header_idx, tabs_idx)
        self.assertLess(tabs_idx, scroll_idx)
        self.assertLess(scroll_idx, footer_idx)
        self.assertIn('href="/work/notebook"', more_html)

        # Filter tab still returns full matching set (not capped at 5).
        blocked = self.store.create(title="Blocked queue item", scope="work")
        self.store.save(
            blocked["id"],
            title="Blocked queue item",
            body_md="",
            note_type="task",
            status="blocked",
            priority="high",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
            scope="work",
        )
        blocked_html = self.client.get("/work?queue=blocked").get_data(as_text=True)
        self.assertIn("Blocked queue item", blocked_html)
        self.assertIn("Showing 1 of 9 open tasks", blocked_html)

        personal_html = self.client.get("/personal").get_data(as_text=True)
        self.assertIn("Personal Task Queue", personal_html)
        self.assertIn("Personal only row", personal_html)
        self.assertNotIn("Queue row 1", personal_html)
        self.assertIn('class="queue-scroll"', personal_html)

        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn("--queue-visible-rows: 5", css)
        self.assertIn(".panel-queue .queue-scroll", css)
        self.assertIn("position: sticky", css)
        self.assertRegex(
            css,
            r"\.panel-queue \.queue-scroll\s*\{[^}]*overflow-y:\s*auto",
        )
        self.assertRegex(
            css,
            r"\.panel-queue \.queue-scroll \.table-wrap\s*\{[^}]*overflow-x:\s*auto",
        )
        self.assertIn("flex: 0 0 auto", css)

    def test_recent_activity_scroll_region(self) -> None:
        """Recent Activity keeps header fixed and scrolls the list body on desktop."""
        r = self.client.get("/work")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Recent Activity", html)
        self.assertIn("panel-activity", html)
        self.assertIn('class="activity-scroll"', html)
        self.assertIn('href="/audit"', html)
        # Header + View all stay outside the scroll region.
        header_idx = html.find("Recent Activity")
        scroll_idx = html.find('class="activity-scroll"')
        view_all_idx = html.find("View all", header_idx)
        self.assertGreater(scroll_idx, 0)
        self.assertGreater(view_all_idx, 0)
        self.assertLess(view_all_idx, scroll_idx)

        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn(".panel-activity .activity-scroll", css)
        self.assertIn("overflow-y: auto", css)
        self.assertIn(".dash-main-stack > .panel-queue", css)
        self.assertIn(".dash-side-stack > .panel-activity", css)
        # Small screens restore normal page scrolling.
        self.assertIn("max-width: 1180px", css)
        self.assertRegex(
            css,
            r"@media \(max-width: 1180px\)[\s\S]*?\.panel-activity \.activity-scroll\s*\{[\s\S]*?overflow:\s*visible",
        )

    def test_personal_dashboard_layout_balance(self) -> None:
        """Personal dashboard keeps 4 top cards balanced and compact empty upcoming."""
        r = self.client.get("/personal")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("Personal Dashboard", html)
        self.assertIn("summary-cols-4", html)
        self.assertIn("Personal Tasks", html)
        self.assertIn("Personal Notes", html)
        self.assertIn("Upcoming Events", html)
        self.assertIn("Audit Events", html)
        # Quick Notepad is on the activity rail; summary-card shortcut removed.
        self.assertIn('id="ar-notepad"', html)
        self.assertIn('id="qn-panel"', html)
        self.assertNotIn('href="/personal#quick-notepad"', html)
        self.assertIn("Upcoming Personal Events", html)
        self.assertIn("Personal Task Queue", html)
        self.assertIn("Recent Activity", html)
        self.assertIn("panel-upcoming", html)
        self.assertIn("dash-grid-personal", html)
        self.assertIn("panel-empty", html)
        self.assertIn("No upcoming events.", html)
        self.assertIn("Connect Calendar", html)

        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn(".summary-grid.summary-cols-4", css)
        self.assertIn("repeat(4, minmax(0, 1fr))", css)
        self.assertIn(".panel-upcoming.is-empty", css)
        self.assertIn(".panel-empty", css)
        self.assertIn(".dash-grid-personal", css)
        self.assertIn("empty-compact", css)

    def test_task_row_status_accent_only(self) -> None:
        """Dashboard + Notebook task rows use left status accents, not full-row tints."""
        note = self.store.create(title="Accent row note")
        self.store.save(
            note["id"],
            title="Accent row note",
            body_md="",
            note_type="task",
            status="pending",
            priority="medium",
            due_date="2026-07-26",
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )

        dash = self.client.get("/work?queue=open").get_data(as_text=True)
        self.assertIn('class="status-pending"', dash)
        self.assertIn("badge-status-pending", dash)
        self.assertIn("Accent row note", dash)

        nb = self.client.get(f"/work/notebook?note={note['id']}").get_data(as_text=True)
        self.assertIn("nb-note-row status-pending", nb)
        self.assertIn("is-active", nb)
        self.assertIn("badge-status-pending", nb)

        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8",
            errors="replace",
        )
        # Left accents by status (Notebook border + Dashboard inset line).
        self.assertIn(".nb-note-row.status-pending { border-left-color: #f59e0b; }", css)
        self.assertIn(".nb-note-row.status-ongoing { border-left-color: #8b5cf6; }", css)
        self.assertIn(".nb-note-row.status-blocked { border-left-color: #ef4444; }", css)
        self.assertIn(".nb-note-row.status-done { border-left-color: #4ade80; }", css)
        self.assertIn("border-left-color: #64748b;", css)
        self.assertIn("inset 3px 0 0 #f59e0b", css)
        self.assertIn("inset 3px 0 0 #8b5cf6", css)
        self.assertIn("inset 3px 0 0 #ef4444", css)
        self.assertIn("inset 3px 0 0 #4ade80", css)
        # No full-row status background tints.
        self.assertNotRegex(
            css,
            r"\.nb-note-row\.status-\w+\s*\{[^}]*background:\s*color-mix",
        )
        self.assertNotRegex(
            css,
            r"queue-table tbody tr\.status-\w+ td\s*\{[^}]*background:\s*color-mix",
        )
        # Selected + keyboard focus remain defined.
        self.assertIn(".nb-note-row.is-active", css)
        self.assertIn(".nb-note-row:focus-visible", css)
        self.assertIn("table.data tbody tr:focus-within", css)
        # Status badges remain colored independently.
        self.assertIn(".badge-status-pending", css)
        self.assertIn(".badge-status-ongoing", css)
        self.assertIn(".badge-status-blocked", css)
        self.assertIn(".badge-status-done", css)

    def test_compact_summary_tiles_and_responsive_layout(self) -> None:
        """Work dashboard summary tiles stay compact and wrap with Okarun / narrow viewports."""
        urgent = self.store.create(title="Urgent dash tile")
        self.store.save(
            urgent["id"],
            title="Urgent dash tile",
            body_md="",
            note_type="task",
            status="pending",
            priority="urgent",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )
        overdue = self.store.create(title="Overdue dash tile")
        self.store.save(
            overdue["id"],
            title="Overdue dash tile",
            body_md="",
            note_type="task",
            status="ongoing",
            priority="medium",
            due_date="2020-01-01",
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )

        html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("summary-compact", html)
        self.assertIn("stat-card-link", html)
        self.assertIn("severity-alert", html)
        self.assertIn("stat-badge", html)
        self.assertIn("stat-status", html)
        self.assertIn("1 urgent", html)
        self.assertIn("1 overdue", html)
        self.assertIn("dash-grid-lower", html)
        self.assertIn("dash-repo-grid", html)
        # Whole tile is the link — no separate footer CTA text required.
        self.assertNotRegex(html, r'class="stat-link"[^>]*>View all')

        css = (Path(__file__).resolve().parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn("min-height: 90px", css)
        self.assertIn("max-height: 110px", css)
        self.assertIn("summary-compact", css)
        self.assertIn("a.stat-card-link", css)
        self.assertIn(".stat-status.is-ok", css)
        self.assertIn(".dash-repo-card", css)
        self.assertIn(
            ".app-shell.is-ad-open:not(.is-ad-mobile):not(.is-ad-minimized) .summary-grid.summary-cols-5",
            css,
        )
        self.assertIn("repeat(auto-fit, minmax(9rem, 1fr))", css)
        self.assertRegex(
            css,
            r"@media \(max-width: 860px\)[\s\S]*?\.summary-grid[\s\S]*?minmax\(8\.5rem",
        )

        # Neutral when open tasks exist but none are urgent/overdue.
        self.store.save(
            urgent["id"],
            title="Urgent dash tile",
            body_md="",
            note_type="task",
            status="done",
            priority="urgent",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )
        self.store.save(
            overdue["id"],
            title="Overdue dash tile",
            body_md="",
            note_type="task",
            status="done",
            priority="medium",
            due_date="2020-01-01",
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )
        open_only = self.store.create(title="Open neutral tile")
        self.store.save(
            open_only["id"],
            title="Open neutral tile",
            body_md="",
            note_type="task",
            status="pending",
            priority="medium",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )
        neutral_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("severity-neutral", neutral_html)
        self.assertIn("0 urgent", neutral_html)
        self.assertIn("0 overdue", neutral_html)

        # Leave no open notes for sibling route tests that expect an empty queue.
        self.store.save(
            open_only["id"],
            title="Open neutral tile",
            body_md="",
            note_type="task",
            status="done",
            priority="medium",
            due_date=None,
            tags="",
            repositories=[],
            checklist=[],
            links=[],
            pinned=False,
        )


if __name__ == "__main__":
    unittest.main()
