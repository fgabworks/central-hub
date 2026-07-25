"""Focused tests for SQL Workspace safety, executor, and library."""

from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from hub.sql_workspace.connections import SqlConnectionProfile, public_error
from hub.sql_workspace.db import SqlWorkspaceDatabase
from hub.sql_workspace.demo import ensure_demo_database
from hub.sql_workspace.executor import SqlExecutor
from hub.sql_workspace.safety import (
    SqlSafetyError,
    bind_named_params,
    extract_named_params,
    validate_readonly_sql,
)
from hub.sql_workspace.store import SqlWorkspaceStore


class SqlSafetyTests(unittest.TestCase):
    def test_allows_select_with_explain(self) -> None:
        self.assertEqual(validate_readonly_sql("SELECT 1").kind, "select")
        self.assertEqual(
            validate_readonly_sql("WITH c AS (SELECT 1 AS x) SELECT * FROM c").kind,
            "with",
        )
        self.assertEqual(validate_readonly_sql("EXPLAIN SELECT 1").kind, "explain")
        self.assertEqual(
            validate_readonly_sql("EXPLAIN ANALYZE SELECT 1").kind, "explain"
        )

    def test_blocks_writes_ddl_copy_call_and_multi(self) -> None:
        blocked = [
            "INSERT INTO t VALUES (1)",
            "UPDATE t SET a = 1",
            "DELETE FROM t",
            "DROP TABLE t",
            "CREATE TABLE t (id INT)",
            "ALTER TABLE t ADD COLUMN x INT",
            "TRUNCATE t",
            "COPY t FROM STDIN",
            "CALL foo()",
            "GRANT SELECT ON t TO u",
            "SELECT 1; SELECT 2",
            "SELECT 1; DELETE FROM t",
        ]
        for sql in blocked:
            with self.subTest(sql=sql):
                with self.assertRaises(SqlSafetyError):
                    validate_readonly_sql(sql)

    def test_blocks_modifying_cte_and_case_bypass(self) -> None:
        with self.assertRaises(SqlSafetyError):
            validate_readonly_sql(
                "WITH c AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM c"
            )
        with self.assertRaises(SqlSafetyError):
            validate_readonly_sql("InSeRt InTo t VaLuEs (1)")
        with self.assertRaises(SqlSafetyError):
            validate_readonly_sql("SELECT 1; /* ok */ DELETE FROM t")

    def test_parameter_binding_rejects_missing_and_keeps_values_bound(self) -> None:
        sql = "SELECT * FROM demo_people WHERE name = :name AND active = :active"
        self.assertEqual(extract_named_params(sql), ["name", "active"])
        bound_sql, params = bind_named_params(
            sql, {"name": "Ada'--; DROP TABLE x;--", "active": 1}
        )
        self.assertEqual(bound_sql, sql)
        self.assertEqual(params["name"], "Ada'--; DROP TABLE x;--")
        with self.assertRaises(SqlSafetyError):
            bind_named_params(sql, {"name": "Ada"})


class SqlExecutorLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.demo = ensure_demo_database(root / "demo.db")
        self.store = SqlWorkspaceStore(SqlWorkspaceDatabase(root / "sql_ws.db"))
        self.executor = SqlExecutor(
            self.store,
            max_rows=5,
            statement_timeout_ms=5000,
            results_dir=root / "results",
        )
        self.profile = SqlConnectionProfile(
            id="local-demo",
            label="Demo",
            environment="dev",
            driver="sqlite",
            enabled=True,
            sqlite_path=str(self.demo),
            configured=True,
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_run_pagination_row_cap_csv_params_and_history(self) -> None:
        result = self.executor.execute(
            self.profile,
            "SELECT id, name FROM demo_people WHERE active = :active ORDER BY id",
            params={"active": 1},
            page=1,
            page_size=1,
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.total_rows, 2)
        self.assertEqual(result.row_count, 1)
        self.assertEqual(result.rows[0][1], "Ada")

        page2 = self.executor.execute(
            self.profile,
            "SELECT id, name FROM demo_people WHERE active = :active ORDER BY id",
            params={"active": 1},
            page=2,
            page_size=1,
        )
        self.assertTrue(page2.ok)
        self.assertEqual(page2.rows[0][1], "Grace")

        # Seed enough rows to hit max_rows=5
        import sqlite3

        conn = sqlite3.connect(str(self.demo))
        conn.executemany(
            "INSERT INTO demo_people (id, name, role, active) VALUES (?, ?, 'x', 1)",
            [(10 + i, f"P{i}") for i in range(10)],
        )
        conn.commit()
        conn.close()
        capped = self.executor.execute(
            self.profile, "SELECT id FROM demo_people ORDER BY id"
        )
        self.assertTrue(capped.ok)
        self.assertTrue(capped.truncated)
        self.assertEqual(capped.total_rows, 5)

        csv_path = self.executor.export_csv_path(capped.run_id)
        self.assertIsNotNone(csv_path)
        assert csv_path is not None
        text = csv_path.read_text(encoding="utf-8")
        self.assertIn("id", text.splitlines()[0])

        runs = self.store.list_runs(limit=20)
        self.assertGreaterEqual(len(runs), 3)

    def test_blocked_sql_records_error_run(self) -> None:
        result = self.executor.execute(self.profile, "DELETE FROM demo_people")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        run = self.store.get_run(result.run_id)
        assert run is not None
        self.assertEqual(run["status"], "error")

    def test_unavailable_connection_message_redacts_secrets(self) -> None:
        bad = SqlConnectionProfile(
            id="bad",
            label="Bad",
            environment="stage",
            driver="postgresql",
            enabled=True,
            host="127.0.0.1",
            port=1,
            database="nope",
            user="ro_user",
            password="super-secret-password",
            configured=True,
        )
        result = self.executor.test_connection(bad)
        self.assertFalse(result["ok"])
        self.assertNotIn("super-secret-password", result["detail"])
        self.assertNotIn(
            "super-secret-password",
            public_error("fail super-secret-password here", bad),
        )

    def test_query_save_version_and_no_autorun(self) -> None:
        created = self.store.create_query(
            title="People",
            sql_text="SELECT 1",
            tags=["demo"],
            favorite=True,
            repository_id="live-processing",
            notebook_note_id="abc",
        )
        self.assertEqual(created["current_version"], 1)
        saved = self.store.save_query(
            created["id"], sql_text="SELECT 2", version_note="bump"
        )
        assert saved is not None
        self.assertEqual(saved["current_version"], 2)
        self.assertEqual(len(saved["versions"]), 2)
        # Saving must not create a run
        self.assertEqual(self.store.list_runs(query_id=created["id"]), [])

    def test_cancel_request_sets_flag(self) -> None:
        run = self.store.create_run(
            connection_id="local-demo", sql_text="SELECT 1", status="running"
        )
        self.assertTrue(self.executor.cancel(run["id"]))
        self.assertTrue(self.store.is_cancel_requested(run["id"]))


class SqlWorkspaceRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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
        cls.app.config["SQL_WS_STORE"] = SqlWorkspaceStore(
            SqlWorkspaceDatabase(root / "sql_ws.db")
        )
        demo = ensure_demo_database(root / "demo.db")
        cls.app.config["SQL_WS_EXECUTOR"] = SqlExecutor(
            cls.app.config["SQL_WS_STORE"],
            max_rows=100,
            results_dir=root / "results",
        )
        # Force local demo path for route tests
        from hub.sql_workspace.connections import SqlConnectionRegistry

        cls.app.config["SQL_WS_CONNECTIONS"] = SqlConnectionRegistry(
            [
                SqlConnectionProfile(
                    id="local-demo",
                    label="Local Demo",
                    environment="dev",
                    driver="sqlite",
                    enabled=True,
                    sqlite_path=str(demo),
                    configured=True,
                )
            ]
        )
        cls.client = cls.app.test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_page_and_run_api(self) -> None:
        page = self.client.get("/sql")
        self.assertEqual(page.status_code, 200)
        html = page.get_data(as_text=True)
        self.assertIn("SQL Workspace", html)
        self.assertIn("Query Library", html)
        self.assertIn("Live connection selected", html)  # warning markup present (hidden)

        created = self.client.post(
            "/api/sql/queries",
            data=json.dumps(
                {
                    "title": "Demo select",
                    "sql": "SELECT id, name FROM demo_people WHERE active = :active",
                    "tags": "demo",
                    "connection_id": "local-demo",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)
        query_id = created.get_json()["query"]["id"]

        # Save does not auto-run
        runs_before = self.client.get(f"/api/sql/runs?query_id={query_id}").get_json()
        self.assertEqual(runs_before["runs"], [])

        run = self.client.post(
            "/api/sql/run",
            data=json.dumps(
                {
                    "connection_id": "local-demo",
                    "sql": "SELECT id, name FROM demo_people WHERE active = :active ORDER BY id",
                    "params": {"active": 1},
                    "query_id": query_id,
                    "page": 1,
                    "page_size": 10,
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(run.status_code, 200)
        payload = run.get_json()
        self.assertTrue(payload["ok"], payload)
        self.assertGreaterEqual(payload["total_rows"], 2)

        blocked = self.client.post(
            "/api/sql/run",
            data=json.dumps(
                {"connection_id": "local-demo", "sql": "DELETE FROM demo_people"}
            ),
            content_type="application/json",
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertFalse(blocked.get_json()["ok"])

        missing = self.client.post(
            "/api/sql/run",
            data=json.dumps(
                {"connection_id": "missing-conn", "sql": "SELECT 1"}
            ),
            content_type="application/json",
        )
        self.assertEqual(missing.status_code, 400)


class SqlWorkspaceRegressionRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
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

        cls.client = create_app().test_client()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_core_routes_still_load(self) -> None:
        for path in (
            "/",
            "/notebook",
            "/repositories",
            "/jobs",
            "/audit",
            "/health",
            "/dhis2",
            "/sql",
            "/settings",
        ):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, path)


if __name__ == "__main__":
    unittest.main()
