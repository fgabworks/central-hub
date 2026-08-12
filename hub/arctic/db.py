"""SQLite persistence for ARCTIC (Personal profile + document registry)."""

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
        "001_arctic_profile_and_registry",
        """
        CREATE TABLE IF NOT EXISTS arctic_profile (
            id TEXT PRIMARY KEY CHECK (id = 'personal'),
            display_name TEXT NOT NULL DEFAULT '',
            headline TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            links_json TEXT NOT NULL DEFAULT '[]',
            skills_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL
        );
        INSERT OR IGNORE INTO arctic_profile
            (id, display_name, headline, email, phone, location, summary,
             links_json, skills_json, updated_at)
        VALUES ('personal', '', '', '', '', '', '', '[]', '[]', datetime('now'));

        CREATE TABLE IF NOT EXISTS arctic_sources (
            id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'deferred',
            detail TEXT NOT NULL DEFAULT '',
            root_path TEXT NOT NULL DEFAULT '',
            last_checked_at TEXT,
            updated_at TEXT NOT NULL
        );

        INSERT OR IGNORE INTO arctic_sources
            (id, source_type, label, status, detail, root_path, last_checked_at, updated_at)
        VALUES
            ('local', 'local', 'Local files', 'ready',
             'References local paths; files stay on disk.', '', NULL, datetime('now')),
            ('google_drive', 'google_drive', 'Google Drive', 'deferred',
             'Drive sync deferred — connect when OAuth Drive scopes are ready.',
             '', NULL, datetime('now'));

        CREATE TABLE IF NOT EXISTS arctic_documents (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            source_label TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '',
            primary_role TEXT NOT NULL DEFAULT '',
            tags_json TEXT NOT NULL DEFAULT '[]',
            is_favorite INTEGER NOT NULL DEFAULT 0,
            needs_attention INTEGER NOT NULL DEFAULT 0,
            attention_reason TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            last_accessed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(source_type, source_ref)
        );
        CREATE INDEX IF NOT EXISTS idx_arctic_docs_role
            ON arctic_documents(primary_role);
        CREATE INDEX IF NOT EXISTS idx_arctic_docs_source
            ON arctic_documents(source_type);
        CREATE INDEX IF NOT EXISTS idx_arctic_docs_favorite
            ON arctic_documents(is_favorite);
        CREATE INDEX IF NOT EXISTS idx_arctic_docs_accessed
            ON arctic_documents(last_accessed_at DESC);
        CREATE INDEX IF NOT EXISTS idx_arctic_docs_updated
            ON arctic_documents(updated_at DESC);
        """,
    ),
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArcticDatabase:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (ROOT_DIR / "data" / "arctic.db")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect_raw(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _ensure_schema(self) -> None:
        with _LOCK:
            conn = self._connect_raw()
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        name TEXT PRIMARY KEY,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                applied = {
                    str(r["name"])
                    for r in conn.execute("SELECT name FROM schema_migrations").fetchall()
                }
                for name, script in _MIGRATIONS:
                    if name in applied:
                        continue
                    conn.executescript(script)
                    conn.execute(
                        "INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                        (name, utcnow()),
                    )
                conn.commit()
            finally:
                conn.close()

    def applied_migrations(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT name FROM schema_migrations ORDER BY applied_at, name"
            ).fetchall()
        return [str(r["name"]) for r in rows]

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
