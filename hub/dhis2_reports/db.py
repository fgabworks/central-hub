"""SQLite persistence for DHIS2 Report Workspace presets, favorites, history."""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from hub.settings import ROOT_DIR

_LOCK = threading.RLock()

_MIGRATIONS: list[tuple[str, str]] = [
    (
        "001_dhis2_reports_initial",
        """
        CREATE TABLE IF NOT EXISTS report_favorites (
            report_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS report_presets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            report_id TEXT NOT NULL,
            environment TEXT NOT NULL,
            period TEXT NOT NULL DEFAULT '',
            org_unit TEXT NOT NULL DEFAULT '',
            parameters_json TEXT NOT NULL DEFAULT '{}',
            output_format TEXT NOT NULL DEFAULT 'html',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_report_presets_report ON report_presets(report_id);

        CREATE TABLE IF NOT EXISTS report_runs (
            id TEXT PRIMARY KEY,
            report_id TEXT NOT NULL,
            report_name TEXT NOT NULL DEFAULT '',
            report_type TEXT NOT NULL DEFAULT '',
            environment TEXT NOT NULL,
            period TEXT NOT NULL DEFAULT '',
            org_unit TEXT NOT NULL DEFAULT '',
            parameters_json TEXT NOT NULL DEFAULT '{}',
            repository_id TEXT NOT NULL DEFAULT '',
            git_branch TEXT NOT NULL DEFAULT '',
            git_commit TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            output_path TEXT NOT NULL DEFAULT '',
            output_url TEXT NOT NULL DEFAULT '',
            hub_job_id TEXT NOT NULL DEFAULT '',
            run_profile_id TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            actor TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            log_text TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_report_runs_started ON report_runs(started_at DESC);
        CREATE INDEX IF NOT EXISTS idx_report_runs_report ON report_runs(report_id);
        CREATE INDEX IF NOT EXISTS idx_report_runs_status ON report_runs(status);
        """,
    ),
    (
        "002_standard_report_sync",
        """
        CREATE TABLE IF NOT EXISTS synced_standard_reports (
            environment TEXT NOT NULL,
            uid TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            report_type TEXT NOT NULL DEFAULT '',
            report_params_json TEXT NOT NULL DEFAULT '{}',
            relative_periods_json TEXT NOT NULL DEFAULT '[]',
            relative_periods_raw_json TEXT NOT NULL DEFAULT '{}',
            data_source_kind TEXT NOT NULL DEFAULT '',
            data_source_id TEXT NOT NULL DEFAULT '',
            data_source_name TEXT NOT NULL DEFAULT '',
            html_design_available INTEGER NOT NULL DEFAULT 0,
            design_content TEXT NOT NULL DEFAULT '',
            cache_strategy TEXT NOT NULL DEFAULT '',
            dhis2_version TEXT NOT NULL DEFAULT '',
            last_synced_at TEXT NOT NULL DEFAULT '',
            last_updated TEXT NOT NULL DEFAULT '',
            created_at_remote TEXT NOT NULL DEFAULT '',
            unsupported_reason TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (environment, uid)
        );
        CREATE INDEX IF NOT EXISTS idx_synced_reports_env ON synced_standard_reports(environment);
        CREATE INDEX IF NOT EXISTS idx_synced_reports_type ON synced_standard_reports(report_type);
        CREATE INDEX IF NOT EXISTS idx_synced_reports_name ON synced_standard_reports(name);

        CREATE TABLE IF NOT EXISTS standard_report_sync_runs (
            id TEXT PRIMARY KEY,
            environment TEXT NOT NULL,
            status TEXT NOT NULL,
            report_count INTEGER NOT NULL DEFAULT 0,
            dhis2_version TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '',
            truncated INTEGER NOT NULL DEFAULT 0,
            capabilities_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_std_sync_runs_env ON standard_report_sync_runs(environment);
        """,
    ),
]


def default_db_path() -> Path:
    configured = (os.environ.get("DHIS2_REPORTS_DATABASE") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else (ROOT_DIR / path)
    return ROOT_DIR / "data" / "dhis2_reports.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportsDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _connect_raw(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = self._connect_raw()
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row["id"]
                for row in conn.execute("SELECT id FROM schema_migrations").fetchall()
            }
            for mid, sql in _MIGRATIONS:
                if mid in applied:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (id, applied_at) VALUES (?, ?)",
                    (mid, utcnow()),
                )
