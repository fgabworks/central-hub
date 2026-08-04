"""TODAY Mission Control — Work Notebook missions and dashboard widget."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from hub.notebook.db import NotebookDatabase
from hub.notebook.missions import MissionControl, ordered_widget_missions
from hub.notebook.store import NotebookStore


class MissionControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = NotebookStore(NotebookDatabase(Path(self.tmp.name) / "notebook.db"))
        self.mc = MissionControl(self.store)
        self.today = datetime(2026, 8, 4, 10, 0, 0)
        self.yesterday = datetime(2026, 8, 3, 10, 0, 0)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_migration_includes_missions(self) -> None:
        applied = self.store.db.applied_migrations()
        self.assertIn("008_today_missions", applied)

    def test_create_mission_and_progress(self) -> None:
        a = self.mc.create_mission(title="Fix HCSC-RF generation", now=self.today)
        b = self.mc.create_mission(title="Review DHIS2 mapping", now=self.today)
        self.assertEqual(a["note_type"], "mission")
        self.assertEqual(a["scope"], "work")
        self.assertEqual(a["due_date"], "2026-08-04")
        self.assertEqual(a["reminder_status"], "none")
        self.assertFalse(a["carry_over"])

        board = self.mc.board(now=self.today, sync=False)
        self.assertEqual(board["progress"]["pending"], 2)
        self.assertEqual(board["progress"]["done"], 0)
        self.assertEqual(board["progress"]["total"], 2)
        self.assertEqual(board["progress"]["label"], "0/2 Completed")

        completed = self.mc.complete_mission(b["id"], now=self.today)
        assert completed is not None
        self.assertEqual(completed["status"], "done")
        self.assertTrue(completed["completed_at"])

        board2 = self.mc.board(now=self.today, sync=False)
        self.assertEqual(board2["progress"]["done"], 1)
        self.assertEqual(board2["progress"]["pending"], 1)
        self.assertEqual(board2["progress"]["total"], 2)

    def test_dashboard_widget_shares_state(self) -> None:
        self.mc.create_mission(title="Validate ECCD PI", now=self.today)
        m = self.mc.create_mission(title="Ship docs", now=self.today)
        self.mc.complete_mission(m["id"], now=self.today)

        board = self.mc.board(now=self.today, sync=False)
        widget = self.mc.widget(now=self.today, sync=False)
        self.assertEqual(widget["progress"], board["progress"])
        self.assertEqual(widget["progress"]["done"], 1)
        titles = [x["title"] for x in widget["top_missions"]]
        self.assertIn("Validate ECCD PI", titles)
        self.assertIn("Ship docs", titles)

    def test_widget_ordering_limit_and_carry_highlight(self) -> None:
        self.mc.create_mission(
            title="Carry me", due_date="2026-08-03", now=self.yesterday
        )
        self.mc.process_carry_over(now=self.today)
        self.mc.create_mission(title="Low unfinished", priority="low", now=self.today)
        self.mc.create_mission(title="High unfinished", priority="high", now=self.today)
        self.mc.create_mission(title="Medium unfinished", priority="medium", now=self.today)
        done_a = self.mc.create_mission(title="Done A", now=self.today)
        done_b = self.mc.create_mission(title="Done B", now=self.today)
        self.mc.complete_mission(done_a["id"], now=self.today)
        self.mc.complete_mission(done_b["id"], now=self.today)
        self.mc.create_mission(title="Extra 1", priority="low", now=self.today)
        self.mc.create_mission(title="Extra 2", priority="low", now=self.today)

        widget = self.mc.widget(now=self.today, sync=False, top_limit=5)
        titles = [m["title"] for m in widget["top_missions"]]
        self.assertEqual(len(titles), 5)
        self.assertTrue(widget["has_more"])
        self.assertGreaterEqual(widget["more_count"], 1)
        self.assertEqual(titles[0], "Carry me")
        self.assertEqual(titles[1], "High unfinished")
        self.assertTrue(widget["top_missions"][0]["is_overdue_carry"])

        ordered = ordered_widget_missions(
            carry_over=[{"title": "C", "priority": "low"}],
            today_open=[
                {"title": "H", "priority": "high"},
                {"title": "L", "priority": "low"},
            ],
            completed_today=[{"title": "D", "priority": "medium", "completed_at": "z"}],
        )
        self.assertEqual([m["title"] for m in ordered], ["C", "H", "L", "D"])

    def test_widget_state_empty_pending_partial_complete_carry(self) -> None:
        empty = self.mc.widget(now=self.today, sync=False)
        self.assertEqual(empty["progress"]["total_all"], 0)
        self.assertEqual(empty["more_count"], 0)

        one = self.mc.create_mission(title="Only one", now=self.today)
        pending = self.mc.widget(now=self.today, sync=False)
        self.assertEqual(pending["progress"]["pending"], 1)
        self.assertEqual(pending["progress"]["done"], 0)

        two = self.mc.create_mission(title="Second", now=self.today)
        self.mc.complete_mission(one["id"], now=self.today)
        partial = self.mc.widget(now=self.today, sync=False)
        self.assertEqual(partial["progress"]["done"], 1)
        self.assertEqual(partial["progress"]["pending"], 1)

        self.mc.complete_mission(two["id"], now=self.today)
        done = self.mc.widget(now=self.today, sync=False)
        self.assertEqual(done["progress"]["done"], 2)
        self.assertEqual(done["progress"]["pending"], 0)
        self.assertEqual(done["progress"]["overdue"], 0)

        old = self.mc.create_mission(
            title="Late", due_date="2026-08-03", now=self.yesterday
        )
        self.mc.process_carry_over(now=self.today)
        carry = self.mc.widget(now=self.today, sync=False)
        self.assertGreaterEqual(carry["progress"]["overdue"], 1)
        self.assertTrue(any(m["id"] == old["id"] for m in carry["top_missions"]))
        self.assertTrue(carry["top_missions"][0]["is_overdue_carry"])

    def test_clear_completed_default_preserves_open(self) -> None:
        open_m = self.mc.create_mission(title="Keep open", now=self.today)
        done = self.mc.create_mission(title="Clear me", now=self.today)
        self.mc.complete_mission(done["id"], now=self.today)
        result = self.mc.clear_missions(mode="completed", now=self.today)
        self.assertEqual(result["mode"], "completed")
        self.assertEqual(result["cleared_count"], 1)
        board = self.mc.board(now=self.today, sync=False)
        titles = {m["title"] for m in board["today_open"]}
        self.assertIn("Keep open", titles)
        self.assertEqual(board["progress"]["done"], 0)
        archived = self.store.get(done["id"])
        assert archived is not None
        self.assertEqual(archived["status"], "archived")
        still = self.store.get(open_m["id"])
        assert still is not None
        self.assertNotEqual(still["status"], "archived")

    def test_clear_all_archives_missions(self) -> None:
        self.mc.create_mission(title="A", now=self.today)
        self.mc.create_mission(title="B", now=self.today)
        result = self.mc.clear_missions(mode="all", now=self.today)
        self.assertEqual(result["mode"], "all")
        self.assertEqual(result["cleared_count"], 2)
        board = self.mc.board(now=self.today, sync=False)
        self.assertEqual(board["progress"]["total_all"], 0)

    def test_reminder_before_5pm(self) -> None:
        self.mc.create_mission(title="Unfinished morning", now=self.today)
        reminded = self.mc.process_reminders(now=datetime(2026, 8, 4, 16, 30, 0))
        self.assertEqual(len(reminded), 1)
        self.assertEqual(reminded[0]["reminder_status"], "sent")
        self.assertEqual(self.mc.process_reminders(now=datetime(2026, 8, 4, 16, 45, 0)), [])
        self.assertEqual(self.mc.process_reminders(now=datetime(2026, 8, 4, 17, 0, 0)), [])

    def test_carry_over_and_red_highlight(self) -> None:
        old = self.mc.create_mission(
            title="Yesterday leftover",
            due_date="2026-08-03",
            now=self.yesterday,
        )
        carried = self.mc.process_carry_over(now=self.today)
        self.assertEqual(len(carried), 1)
        self.assertTrue(carried[0]["carry_over"])
        board = self.mc.board(now=self.today, sync=False)
        self.assertEqual(board["progress"]["overdue"], 1)
        self.assertTrue(board["carry_over"][0]["is_overdue_carry"])
        rescheduled = self.mc.reschedule_mission(
            old["id"], due_date="2026-08-04", now=self.today
        )
        assert rescheduled is not None
        self.assertFalse(rescheduled["carry_over"])
        self.assertEqual(rescheduled["original_due_date"], "2026-08-03")

    def test_existing_notebook_notes_still_work(self) -> None:
        note = self.store.create(title="Regular note", scope="work", note_type="task")
        saved = self.store.save(
            note["id"],
            title="Regular note",
            body_md="still works",
            note_type="task",
            status="ongoing",
            priority="high",
            due_date="2026-08-04",
            tags="hub",
            repositories=[],
            checklist=[{"text": "Check", "done": False}],
            links=[],
            pinned=True,
        )
        assert saved is not None
        self.assertEqual(saved["note_type"], "task")
        board = self.mc.board(now=self.today, sync=False)
        self.assertEqual(board["progress"]["total"], 0)


class MissionControlRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import importlib

        import hub.settings as settings_mod
        import app as app_mod

        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name)

        importlib.reload(settings_mod)
        importlib.reload(app_mod)
        from app import create_app

        cls.app = create_app()
        cls.app.config["TESTING"] = True
        cls.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(root / "notebook.db"))
        cls.client = cls.app.test_client()

    def setUp(self) -> None:
        db_path = Path(self._tmp.name) / f"{self._testMethodName}.db"
        self.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(db_path))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_create_complete_and_pages_sync(self) -> None:
        created = self.client.post(
            "/api/notebook/missions",
            json={"title": "Fix HCSC-RF generation", "priority": "high"},
        )
        self.assertEqual(created.status_code, 200)
        mission_id = created.get_json()["mission"]["id"]

        board = self.client.get("/api/notebook/missions/board").get_json()["board"]
        widget = self.client.get("/api/notebook/missions/widget").get_json()["widget"]
        self.assertEqual(board["progress"]["pending"], widget["progress"]["pending"])

        done = self.client.post(f"/api/notebook/missions/{mission_id}/complete")
        self.assertEqual(done.status_code, 200)
        body = done.get_json()
        self.assertEqual(body["mission"]["status"], "done")
        self.assertIn("widget", body)

        board2 = self.client.get("/api/notebook/missions/board").get_json()["board"]
        widget2 = self.client.get("/api/notebook/missions/widget").get_json()["widget"]
        self.assertEqual(board2["progress"], widget2["progress"])

        nb = self.client.get("/work/notebook?view=missions")
        self.assertEqual(nb.status_code, 200)
        self.assertIn(b"Fix HCSC-RF generation", nb.data)

        dash = self.client.get("/work")
        self.assertEqual(dash.status_code, 200)
        html = dash.get_data(as_text=True)
        self.assertIn("TODAY Mission Control", html)
        self.assertIn("Open Mission Control", html)
        self.assertIn("mc-command", html)
        self.assertIn("mc-progress-ring", html)
        self.assertIn("mc-widget-checkbox", html)
        self.assertIn("dash-grid-mission-top", html)
        self.assertIn("What needs to be done today?", html)
        self.assertIn("+ Add Mission", html)
        self.assertIn("Clear Completed", html)
        self.assertIn("data-mc-clear-completed", html)
        self.assertIn("mission_widget.js", html)
        self.assertIn("mc-command-list", html)
        self.assertIn("Open Mission Control", html)

        notes = self.client.get("/work/notebook")
        self.assertEqual(notes.status_code, 200)
        self.assertIn(b"All Notes", notes.data)

    def test_dashboard_widget_requested_layout_states(self) -> None:
        empty_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("No missions today.", empty_html)
        self.assertEqual(empty_html.count('class="mc-command-item'), 0)

        one = self.client.post(
            "/api/notebook/missions",
            json={"title": "ECCD Denominator", "priority": "medium"},
        ).get_json()["mission"]
        one_html = self.client.get("/work").get_data(as_text=True)
        self.assertEqual(one_html.count('class="mc-command-item'), 1)
        self.assertIn("ECCD Denominator", one_html)
        self.assertIn("Medium", one_html)
        self.assertIn("Pending", one_html)
        self.assertIn("data-mc-add-form", one_html)
        self.assertNotIn("mc-progress-track-inline", one_html)
        self.assertIn("mc-header-progress", one_html)
        self.assertLess(
            one_html.index('id="mc-dash-widget"'),
            one_html.index('class="panel panel-queue'),
        )
        self.assertNotIn("dash-grid-queue-row", one_html)

        for index in range(2, 6):
            self.client.post(
                "/api/notebook/missions",
                json={"title": f"Mission {index}", "priority": "medium"},
            )
        five_html = self.client.get("/work").get_data(as_text=True)
        self.assertEqual(five_html.count('class="mc-command-item'), 5)

        sixth = self.client.post(
            "/api/notebook/missions",
            json={"title": "Sixth mission", "priority": "medium"},
        ).get_json()["widget"]
        self.assertEqual(len(sixth["top_missions"]), 5)
        self.assertEqual(len(sixth["missions"]), 6)
        six_html = self.client.get("/work").get_data(as_text=True)
        self.assertEqual(six_html.count('class="mc-command-item'), 5)
        widget_html = six_html[
            six_html.index('id="mc-dash-widget"') :
            six_html.index('class="panel panel-queue')
        ]
        visible_titles = {mission["title"] for mission in sixth["top_missions"]}
        omitted_titles = {
            mission["title"] for mission in sixth["missions"]
        } - visible_titles
        self.assertTrue(omitted_titles)
        self.assertTrue(all(title in widget_html for title in visible_titles))
        self.assertTrue(all(title not in widget_html for title in omitted_titles))
        css = (Path(__file__).parents[1] / "static" / "css" / "style.css").read_text(
            encoding="utf-8"
        )
        list_rules = css.split(".mc-command-list {", 1)[1].split("}", 1)[0]
        card_rules = css.split(".panel-mission-widget.mc-command {", 1)[1].split(
            "}", 1
        )[0]
        self.assertNotIn("max-height:", list_rules)
        self.assertNotIn("min-height:", list_rules)
        self.assertNotIn("height:", list_rules)
        self.assertNotIn("height:", card_rules)
        self.assertNotIn("min-height:", card_rules)

        for mission in sixth["missions"]:
            self.client.post(f"/api/notebook/missions/{mission['id']}/complete")
        done_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("is-success", done_html)
        self.assertIn("All done today", done_html)
        self.assertIn("Completed", done_html)
        self.assertIn("Medium", done_html)

        self.client.post(
            "/api/notebook/missions",
            json={
                "title": "Yesterday carry-over",
                "priority": "high",
                "due_date": "2026-08-03",
            },
        )
        carry_html = self.client.get("/work").get_data(as_text=True)
        self.assertIn("is-carry-warn", carry_html)
        self.assertIn("is-carry", carry_html)
        self.assertIn("Carry-over", carry_html)

    def test_dashboard_checkbox_complete_syncs_notebook(self) -> None:
        created = self.client.post(
            "/api/notebook/missions",
            json={"title": "Dash checkbox mission", "priority": "urgent"},
        ).get_json()["mission"]
        mission_id = created["id"]

        complete = self.client.post(f"/api/notebook/missions/{mission_id}/complete")
        self.assertEqual(complete.status_code, 200)
        body = complete.get_json()
        widget = body["widget"]
        self.assertEqual(body["mission"]["status"], "done")
        self.assertTrue(body["mission"].get("completed_at"))
        self.assertGreaterEqual(widget["progress"]["done"], 1)

        board = self.client.get("/api/notebook/missions/board").get_json()["board"]
        self.assertEqual(board["progress"], widget["progress"])
        self.assertTrue(any(m["id"] == mission_id for m in board["completed_today"]))

        nb = self.client.get("/work/notebook?view=missions")
        self.assertIn(b"Dash checkbox mission", nb.data)

    def test_dashboard_create_mission_defaults_today_medium(self) -> None:
        created = self.client.post(
            "/api/notebook/missions",
            json={"title": "From dashboard widget"},
        )
        self.assertEqual(created.status_code, 200)
        payload = created.get_json()
        mission = payload["mission"]
        self.assertEqual(mission["priority"], "medium")
        self.assertEqual(mission["note_type"], "mission")
        self.assertTrue(mission.get("due_date"))
        self.assertIn("widget", payload)
        titles = [m["title"] for m in payload["widget"]["top_missions"]]
        self.assertIn("From dashboard widget", titles)

        board = self.client.get("/api/notebook/missions/board").get_json()["board"]
        self.assertTrue(
            any(m["title"] == "From dashboard widget" for m in board["today_open"])
            or any(
                m["title"] == "From dashboard widget"
                for m in board["completed_today"]
            )
        )

        dash = self.client.get("/work")
        self.assertIn(b"From dashboard widget", dash.data)
        self.assertIn(b"data-mc-add-form", dash.data)

    def test_clear_requires_confirmation(self) -> None:
        self.client.post("/api/notebook/missions", json={"title": "Temp"})
        denied = self.client.post(
            "/api/notebook/missions/clear", json={"mode": "completed"}
        )
        self.assertEqual(denied.status_code, 400)

        created = self.client.post(
            "/api/notebook/missions", json={"title": "Done for clear"}
        ).get_json()["mission"]
        self.client.post(f"/api/notebook/missions/{created['id']}/complete")
        ok = self.client.post(
            "/api/notebook/missions/clear",
            json={"mode": "completed", "confirm": "clear-completed"},
        )
        self.assertEqual(ok.status_code, 200)
        payload = ok.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "completed")
        self.assertGreaterEqual(payload["cleared_count"], 1)
        self.assertEqual(payload["widget"]["progress"]["done"], 0)

        denied_all = self.client.post(
            "/api/notebook/missions/clear", json={"mode": "all"}
        )
        self.assertEqual(denied_all.status_code, 400)


if __name__ == "__main__":
    unittest.main()
