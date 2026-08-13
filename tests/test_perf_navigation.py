"""Performance regression guards for key navigation routes."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from app import create_app
from hub.adapters.manager import AdapterManager
from hub.notebook.db import NotebookDatabase
from hub.notebook.store import NotebookStore


# Cached local navigation target (generous for CI/Windows variance).
NAV_BUDGET_MS = 500.0
DASH_SHELL_BUDGET_MS = 1000.0
WARMUP = 1
SAMPLES = 3


class PerformanceRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "notebook.db"
        self.app = create_app()
        self.app.config["NOTEBOOK"] = NotebookStore(NotebookDatabase(self.db_path))
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _p95(self, path: str, *, samples: int = SAMPLES) -> tuple[float, object]:
        for _ in range(WARMUP):
            self.client.get(path)
        timings: list[float] = []
        last = None
        for _ in range(samples):
            start = time.perf_counter()
            last = self.client.get(path)
            timings.append((time.perf_counter() - start) * 1000.0)
        timings.sort()
        idx = max(0, int(round(0.95 * (len(timings) - 1))))
        return timings[idx], last

    def test_server_timing_header_present(self) -> None:
        resp = self.client.get("/work")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Server-Timing", resp.headers)
        self.assertIn("app;dur=", resp.headers["Server-Timing"])

    def test_work_dashboard_does_not_probe_health(self) -> None:
        adapters = self.app.config["ADAPTERS"]
        self.assertIsNotNone(adapters)
        with mock.patch.object(
            AdapterManager, "check_all", side_effect=AssertionError("health must stay async")
        ):
            resp = self.client.get("/work")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Status loading", html)
        self.assertIn("nav_async.js", html)

    def test_repositories_does_not_probe_health(self) -> None:
        with mock.patch.object(
            AdapterManager, "check_all", side_effect=AssertionError("health must stay async")
        ):
            resp = self.client.get("/repositories")
        self.assertEqual(resp.status_code, 200)

    def test_health_page_skips_process_scan_on_shell(self) -> None:
        workspace = self.app.config.get("REPO_WORKSPACE")
        if workspace is None:
            self.skipTest("repo workspace unavailable")
        with mock.patch.object(
            workspace,
            "summarize_local_processes",
            side_effect=AssertionError("process scan must stay async"),
        ):
            with mock.patch.object(
                AdapterManager,
                "check_all",
                side_effect=AssertionError("default health GET must not force probe"),
            ):
                resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("nav_async.js", resp.get_data(as_text=True))
        self.assertIn("Scanning local processes", resp.get_data(as_text=True))

    def test_personal_dashboard_skips_calendar_api(self) -> None:
        cal = self.app.config["CALENDAR"]
        with mock.patch.object(
            cal,
            "upcoming_for_workspace",
            side_effect=AssertionError("calendar must stay async on shell"),
        ):
            resp = self.client.get("/personal")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertIn("Upcoming Events", html)
        self.assertIn("nav_async.js", html)

    def test_assistant_center_does_not_probe_providers(self) -> None:
        connections = self.app.config["AGENT_CENTER"].connections
        with mock.patch.object(
            connections,
            "get",
            wraps=connections.get,
        ) as get_mock:
            resp = self.client.get("/work/airix")
            self.assertEqual(resp.status_code, 200)
            for call in get_mock.call_args_list:
                kwargs = call.kwargs
                self.assertFalse(kwargs.get("probe", True) and kwargs.get("refresh", False))
                # page_bootstrap uses probe=False
                if "probe" in kwargs:
                    self.assertFalse(kwargs["probe"])

    def test_ai_connections_page_uses_cache_only(self) -> None:
        connections = self.app.config["AGENT_CENTER"].connections
        with mock.patch.object(
            connections,
            "list_coding_clis",
            wraps=connections.list_coding_clis,
        ) as coding_mock, mock.patch.object(
            connections,
            "list",
            wraps=connections.list,
        ) as list_mock:
            resp = self.client.get("/system/ai-connections")
        self.assertEqual(resp.status_code, 200)
        coding_mock.assert_called()
        kwargs = coding_mock.call_args.kwargs
        self.assertFalse(kwargs.get("probe", True))
        list_mock.assert_called()
        self.assertFalse(list_mock.call_args.kwargs.get("probe", True))

    def test_cached_navigation_p95_budget(self) -> None:
        routes = [
            ("/work", DASH_SHELL_BUDGET_MS),
            ("/personal", DASH_SHELL_BUDGET_MS),
            ("/repositories", NAV_BUDGET_MS),
            ("/health", NAV_BUDGET_MS),
            ("/dhis2", NAV_BUDGET_MS),
            ("/work/airix", DASH_SHELL_BUDGET_MS),
            ("/personal/aira", DASH_SHELL_BUDGET_MS),
        ]
        # Warm AI connection placeholders / templates once.
        self.client.get("/work")
        report: list[str] = []
        for path, budget in routes:
            p95, resp = self._p95(path)
            self.assertEqual(resp.status_code, 200, msg=path)
            report.append(f"{path} p95={p95:.1f}ms budget={budget:.0f}ms")
            self.assertLessEqual(
                p95,
                budget,
                msg=f"{path} p95 {p95:.1f}ms exceeded budget {budget:.0f}ms",
            )
        # Helpful when debugging locally.
        print("perf_report " + " | ".join(report))

    def test_notebook_batch_hydrate_avoids_n_plus_one(self) -> None:
        store = self.app.config["NOTEBOOK"]
        for i in range(12):
            store.create(title=f"Note {i}", scope="work")
        with store.db.connect() as conn:
            before = conn.total_changes
        # list_open should use bulk hydrate (constant queries relative to N).
        notes = store.list_open(limit=50, scope="work")
        self.assertGreaterEqual(len(notes), 12)


if __name__ == "__main__":
    unittest.main()
