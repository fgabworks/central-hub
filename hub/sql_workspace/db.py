"""SQLite persistence for SQL Workspace library and run history."""

from __future__ import annotations

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
        "001_sql_workspace_initial",
        """
        CREATE TABLE IF NOT EXISTS sql_folders (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_id TEXT,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(parent_id) REFERENCES sql_folders(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS sql_queries (
            id TEXT PRIMARY KEY,
            folder_id TEXT,
            title TEXT NOT NULL DEFAULT 'Untitled query',
            description TEXT NOT NULL DEFAULT '',
            sql_text TEXT NOT NULL DEFAULT '',
            connection_id TEXT NOT NULL DEFAULT '',
            favorite INTEGER NOT NULL DEFAULT 0,
            tags_json TEXT NOT NULL DEFAULT '[]',
            repository_id TEXT NOT NULL DEFAULT '',
            notebook_note_id TEXT NOT NULL DEFAULT '',
            current_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(folder_id) REFERENCES sql_folders(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sql_queries_folder ON sql_queries(folder_id);
        CREATE INDEX IF NOT EXISTS idx_sql_queries_fav ON sql_queries(favorite);
        CREATE INDEX IF NOT EXISTS idx_sql_queries_updated ON sql_queries(updated_at DESC);

        CREATE TABLE IF NOT EXISTS sql_query_versions (
            id TEXT PRIMARY KEY,
            query_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            sql_text TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            FOREIGN KEY(query_id) REFERENCES sql_queries(id) ON DELETE CASCADE,
            UNIQUE(query_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_sql_versions_query ON sql_query_versions(query_id, version DESC);

        CREATE TABLE IF NOT EXISTS sql_runs (
            id TEXT PRIMARY KEY,
            query_id TEXT NOT NULL DEFAULT '',
            connection_id TEXT NOT NULL,
            environment TEXT NOT NULL DEFAULT '',
            sql_text TEXT NOT NULL,
            params_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            row_count INTEGER,
            duration_ms REAL,
            error TEXT,
            columns_json TEXT NOT NULL DEFAULT '[]',
            result_path TEXT NOT NULL DEFAULT '',
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            finished_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sql_runs_created ON sql_runs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_sql_runs_query ON sql_runs(query_id);
        """,
    ),
]


def default_sql_workspace_db_path() -> Path:
    return ROOT_DIR / "data" / "sql_workspace.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class SqlWorkspaceDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_sql_workspace_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    def _migrate(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                str(row["name"])
                for row in conn.execute("SELECT name FROM schema_migrations").fetchall()
            }
            for name, sql in _MIGRATIONS:
                if name in applied:
                    continue
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                    (name, utcnow()),
                )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(str(self.path), timeout=30)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def applied_migrations(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM schema_migrations ORDER BY id"
            ).fetchall()
        return [str(r["name"]) for r in rows]
